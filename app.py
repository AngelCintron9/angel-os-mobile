import streamlit as st
import google.generativeai as genai
from google.cloud import firestore
import vertexai
from vertexai.preview.vision_models import ImageGenerationModel
import os
import time
import json
from PIL import Image
import pypdf
from streamlit_mic_recorder import mic_recorder
import google.auth
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import hashlib
import asyncio
import edge_tts
from datetime import datetime, timezone, timedelta
import pandas as pd

# ---------------------------------------------------------
# 1. CONFIGURACIÓN INICIAL
# ---------------------------------------------------------
st.set_page_config(page_title="Angel OS - Jarvis", page_icon="🎙️", layout="wide")

# Inicializar Variables de Estado
if "messages" not in st.session_state: st.session_state.messages = []
if "last_audio_hash" not in st.session_state: st.session_state.last_audio_hash = None
if "doc_text" not in st.session_state: st.session_state.doc_text = ""
if "image_data" not in st.session_state: st.session_state.image_data = None
if "generated_image_cache" not in st.session_state: st.session_state.generated_image_cache = None
if "core_memory_cache" not in st.session_state: st.session_state.core_memory_cache = None

# ---------------------------------------------------------
# 2. AUTENTICACIÓN HÍBRIDA (LA CLAVE DEL ÉXITO)
# ---------------------------------------------------------
PROJECT_ID = "jarvis-ia-v1"
SCOPES = [
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/cloud-platform'
]

@st.cache_resource
def get_google_credentials():
    """
    Recupera la 'Llave Maestra' desde los Secretos de Streamlit.
    Esta credencial sirve para: Calendar, Firestore y Vertex AI.
    """
    if "token_json" in st.secrets:
        try:
            json_str = st.secrets["token_json"]["json_content"]
            token_info = json.loads(json_str)
            
            # 2. Reconstruimos la credencial
            creds = Credentials.from_authorized_user_info(info=token_info, scopes=SCOPES)
            
            # 3. Auto-Refresco (Vital para que Jarvis no muera en 1 hora)
            if creds and creds.expired and creds.refresh_token:
                from google.auth.transport.requests import Request
                try:
                    creds.refresh(Request())
                    # print("🔄 Token refrescado automáticamente")
                except Exception as e:
                    st.error(f"⚠️ Error refrescando token: {e}")
            
            return creds
        except Exception as e:
            st.error(f"❌ Error procesando el Token Maestro: {e}")
            return None
    
    # Fallback para desarrollo local (si tienes el archivo token.json en la carpeta)
    elif os.path.exists("token.json"):
        return Credentials.from_authorized_user_info(info=json.load(open("token.json")), scopes=SCOPES)
        
    return None

# --- INICIALIZAR SERVICIOS ---
creds_db = get_db_credentials() # Credenciales complejas para DB
api_key = st.secrets.get("GOOGLE_API_KEY") # Clave simple para Chat

# 1. Firestore (Base de Datos)
db = None
if creds_db:
    try:
        db = firestore.Client(credentials=creds_db, project=PROJECT_ID)
    except Exception as e: st.error(f"Error conectando DB: {e}")

# 2. Vertex AI (Solo para generar imágenes)
if creds_db:
    try:
        vertexai.init(project=PROJECT_ID, location="us-central1", credentials=creds_db)
    except: pass

# 3. Configurar Chat (Gemini) - Usamos API Key por velocidad
if api_key:
    genai.configure(api_key=api_key)
else:
    st.error("⚠️ Falta GOOGLE_API_KEY en Secrets")

# ---------------------------------------------------------
# 3. HERRAMIENTAS (TOOLS)
# ---------------------------------------------------------
zona_pr = timezone(timedelta(hours=-4))
fecha_ui = datetime.now(zona_pr).strftime("%A, %d de %B de %Y - %I:%M %p")

def get_current_time():
    """Devuelve la fecha y hora exacta actual en Puerto Rico."""
    fecha_exacta = datetime.now(zona_pr).strftime("%A, %d de %B de %Y - %I:%M:%S %p")
    return f"La fecha y hora actual es: {fecha_exacta}"

def update_core_memory(hecho, categoria="General"):
    """Guarda datos vitales en la memoria a largo plazo."""
    if not db: return "❌ Error: DB desconectada."
    try:
        db.collection('memoria_central').document(categoria).set({
            "recuerdos": firestore.ArrayUnion([hecho]),
            "ultima_actualizacion": time.time()
        }, merge=True)
        
        # Actualizar caché local
        if st.session_state.core_memory_cache is None: st.session_state.core_memory_cache = ""
        st.session_state.core_memory_cache += f"\n- [{categoria}]: {hecho}"
        return f"🧠 Recuerdo guardado en [{categoria}]: '{hecho}'"
    except Exception as e:
        return f"❌ Error memoria: {str(e)}"

def add_event_to_google(summary, start_time, duration_minutes=60):
    """Crea eventos en Google Calendar."""
    if not creds: return "❌ Error: Sin credenciales."
    try:
        service = build('calendar', 'v3', credentials=creds)
        
        # Parseo de fecha flexible
        try:
            if "T" in start_time: start_dt = datetime.fromisoformat(start_time)
            else: start_dt = datetime.fromisoformat(f"{start_time}T09:00:00")
        except: return f"❌ Formato de fecha inválido: {start_time}"

        end_dt = start_dt + timedelta(minutes=duration_minutes)
        event = {
            'summary': summary,
            'start': {'dateTime': start_dt.isoformat(), 'timeZone': 'America/Puerto_Rico'},
            'end': {'dateTime': end_dt.isoformat(), 'timeZone': 'America/Puerto_Rico'},
        }
        res = service.events().insert(calendarId='primary', body=event).execute()
        return f"✅ Evento '{summary}' creado. Link: {res.get('htmlLink')}"
    except Exception as e:
        return f"❌ Error Calendar: {str(e)}"

def add_task_to_board(tarea, estado="🚀 Por hacer", prioridad="🔵 Media", fecha=""):
    if not db: return "❌ Error: Base de datos desconectada."
    try:
        db.collection('proyectos').add({"Tarea": tarea, "Estado": estado, "Prioridad": prioridad, "Fecha": fecha})
        return f"✅ Tarea guardada: {tarea}"
    except Exception as e: return f"Error DB: {e}"

def read_board_tasks(filtro_estado=""):
    if not db: return "❌ DB Desconectada."
    try:
        docs = db.collection('proyectos').stream()
        tareas = [f"- {d.to_dict().get('Tarea')}" for d in docs]
        return "\n".join(tareas) if tareas else "📭 Tablero vacío."
    except: return "Error leyendo tareas."

def generate_creative_image(prompt_visual):
    """Genera imágenes con Imagen 3 Fast."""
    if not creds: return "❌ Error: Sin credenciales Vertex."
    print(f"🎨 Generando: {prompt_visual[:30]}...")
    try:
        model = ImageGenerationModel.from_pretrained("imagen-3.0-fast-generate-001")
        images = model.generate_images(
            prompt=prompt_visual, number_of_images=1, language="es", aspect_ratio="16:9"
        )
        if images:
            st.session_state.generated_image_cache = images[0]
            return "✅ Imagen generada."
        return "⚠️ No se generó imagen."
    except Exception as e:
        return f"❌ Error Imagen: {str(e)}"
        
def save_book_knowledge(titulo, aprendizajes_clave):
    if not db: return "❌ Error DB."
    try:
        db.collection('biblioteca').document(titulo).set({
            "resumen": aprendizajes_clave,
            "fecha": datetime.now().strftime("%Y-%m-%d")
        })
        return f"📚 Libro '{titulo}' archivado."
    except Exception as e: return f"Error: {e}"

# Mapa de herramientas para Gemini
mis_herramientas = [
    get_current_time, update_core_memory, add_event_to_google, 
    add_task_to_board, read_board_tasks, generate_creative_image, save_book_knowledge
]

# ---------------------------------------------------------
# 4. GESTOR DE PROYECTOS (UI)
# ---------------------------------------------------------
def gestor_de_proyectos():
    st.header("📊 Tablero de Mando")
    if not db: st.error("Sin conexión a DB"); return
    
    try:
        docs = db.collection('proyectos').stream()
        items = [{'id': doc.id, **doc.to_dict()} for doc in docs]
        df = pd.DataFrame(items) if items else pd.DataFrame(columns=['Tarea', 'Estado', 'Prioridad', 'Fecha', 'id'])
        
        # Limpieza de columnas
        for col in ['Tarea', 'Estado', 'Prioridad', 'Fecha', 'id']:
            if col not in df.columns: df[col] = None
        
        df['Fecha'] = pd.to_datetime(df['Fecha'], errors='coerce')
        
        edited_df = st.data_editor(
            df[['Tarea', 'Estado', 'Prioridad', 'Fecha', 'id']],
            num_rows="dynamic",
            column_config={
                "Estado": st.column_config.SelectboxColumn("Estado", options=["🚀 Por hacer", "⚙️ En Progreso", "✅ Completado"]),
                "Prioridad": st.column_config.SelectboxColumn("Prioridad", options=["🔥 Alta", "🔵 Media", "🟢 Baja"]),
                "Fecha": st.column_config.DateColumn("Fecha Límite"),
                "id": st.column_config.Column(disabled=True),
            },
            hide_index=True,
            key="editor_proyectos"
        )
        
        if st.button("💾 Guardar Cambios"):
            with st.spinner("Sincronizando..."):
                edited_df['Fecha'] = edited_df['Fecha'].apply(lambda x: x.strftime('%Y-%m-%d') if pd.notnull(x) else None)
                records = edited_df.to_dict(orient='records')
                col_ref = db.collection('proyectos')
                
                # Lógica simple de guardado (Upsert)
                for record in records:
                    doc_id = record.pop('id', None)
                    if doc_id and len(str(doc_id)) > 5: col_ref.document(doc_id).set(record)
                    elif str(record.get('Tarea', '')).strip(): col_ref.add(record)
                
                st.success("✅ Tablero Actualizado"); time.sleep(1); st.rerun()
                
    except Exception as e: st.error(f"Error cargando tablero: {e}")

# ---------------------------------------------------------
# 5. FUNCIONES DE MEMORIA Y GESTIÓN
# ---------------------------------------------------------
DOCUMENT_ID = "memoria_jarvis_v2"

def save_message(role, content):
    """Guarda el mensaje en Firestore"""
    if db:
        try:
            doc_ref = db.collection("conversaciones").document(DOCUMENT_ID)
            text_to_save = content if isinstance(content, str) else "[Contenido Multimodal]"
            doc_ref.set({
                "messages": firestore.ArrayUnion([{"role": role, "content": text_to_save, "timestamp": time.time()}])
            }, merge=True)
        except Exception as e:
            print(f"Error guardando chat: {e}")

# --- BÓVEDA DE MEMORIA CENTRAL ---
if "core_memory_cache" not in st.session_state: st.session_state.core_memory_cache = None

def load_core_memory():
    """Lee la bóveda de Firestore"""
    if not db: return ""
    try:
        memoria_texto = ""
        docs = db.collection('memoria_central').stream()
        for doc in docs:
            datos = doc.to_dict()
            recuerdos = datos.get("recuerdos", [])
            if recuerdos:
                memoria_texto += f"\n- [{doc.id}]: " + " | ".join(recuerdos)
        return memoria_texto
    except Exception as e:
        return ""

if st.session_state.core_memory_cache is None:
    st.session_state.core_memory_cache = load_core_memory()

# ---------------------------------------------------------
# 6. INTERFAZ Y PROMPTS
# ---------------------------------------------------------
# ---------------------------------------------------------
# PERSONALIDADES (PROMPTS)
# ---------------------------------------------------------
# ==========================================
# 🧠 CEREBRO 1: JARVIS (VIDA & ADMIN)
# ==========================================
PROMPT_JARVIS = """
## ROL PRINCIPAL
Eres Jarvis, el Arquitecto de Vida, Operaciones y Longevidad de Angel Cintron. No eres un simple chatbot; eres un mentor de Alto Rendimiento y un Secretario Ejecutivo de élite fusionados en uno.

Tu propósito es eliminar el caos (administrativo y mental) para que Angel pueda operar como un visionario. Tu gestión abarca: Salud (Bio-individualidad), Riqueza (Trading/Home Depot), Sabiduría (Estudio IA) y el crecimiento espiritual.

## 🛠️ DIRECTIVAS DE SISTEMA OPERATIVO (USO DE HERRAMIENTAS)
Eres una IA proactiva con acceso a herramientas reales. DEBES usarlas siempre que sea pertinente:
1.  **El Reloj:** Si necesitas saber la hora exacta para la rutina, o si Angel te pregunta qué hora es o cuánto falta para algo, USA TU HERRAMIENTA `get_current_time`. No intentes adivinar.
2.  **El Calendario:** Si Angel te pide agendar, planificar o bloquear espacio, USA TU HERRAMIENTA `add_event_to_google`. Pide confirmación de la hora primero.
3.  **El Tablero de Mando:** Si Angel tiene una nueva misión, idea o pendiente, USA TU HERRAMIENTA `add_task_to_board`. Si te pregunta qué tareas tiene atrasadas o pendientes, USA `read_board_tasks`.
4.  **La Memoria Central:** Si Angel te dice algo importante sobre sus metas, gustos, reglas o personas clave, USA TU HERRAMIENTA `update_core_memory` para tatuarlo en tu cerebro a largo plazo.

## 📚 TU BASE DE CONOCIMIENTO (FILOSOFÍA)
Tus consejos y tono deben respirar la esencia de estos programas:
1.  **Longevidad:** "Zonas Azules" (Live to 100) y "Limitless" (Chris Hemsworth).
2.  **Eficiencia:** "Hábitos Atómicos" (Mejora 1 porcentaje diario).
3.  **Mente:** "El poder de tu mente subconsciente" y "Club de las 5 AM".
4.  **Finanzas:** "Un millón al año no hace daño" (Yoel Sardinas).
5.  **Estudio:** Google AI Skills

## 👤 CONTEXTO DEL USUARIO (Angel)
DATOS VITALES:
- Usa siempre esta fecha como tu "hoy" para cualquier cálculo de calendario o tareas.
- **Ubicación:** Puerto Rico (Zona de Siembra 11-13, Clima Tropical).
- **Profesión:** Trader (L-V Apertura mercado) + Home Depot (Rotativo) + Fotógrafo (@nano.aventuras).
- **Días Libres:** Generalmente Martes y Sábados (Sagrados para Naturaleza/Proyectos).
- **Intereses:** Huerto, Calistenia, Yoga, Guitarra, Podcast (Diego Dreyfus, BBVA aprendemos juntos, Dot CSV).

## 📅 PROTOCOLOS DE TIEMPO (REGLAS FIJAS)
1.  **El Ritual del Domingo (7:00 AM):** Debes entregar el MAPA GENERAL de la semana. Pide el horario de Home Depot si no lo tienes.
2.  **El Chequeo Diario (1:00 PM):** A esta hora exacta, pregúntale activamente: *"Angel, ¿listo para estructurar la rutina de mañana?"*.
    * *Una vez confirmado:* Genera la rutina detallada de 4:45 AM a 10:00 PM.
3.  **Confirmación:** Antes de dar por cerrada una agenda o tarea, pide confirmación ("¿Te parece bien esta estructura o ajustamos?").

## ⚙️ TUS 4 MOTORES DE OPERACIÓN

### MOTOR 1: VIDA Y SALUD (El Mentor)
- **Estructura de Rutina:**
    * [04:45-07:00] Victoria Privada (Club 5AM, Meditación, Ejercicio).
    * [09:00-12:30] Bloque Riqueza (Trading - Solo si mercado abre).
    * [Bloque Variable] Home Depot / Estudio IA / Proyectos.
    * [Cierre] Reflexión y Desconexión.
- **Nutrición:** Si Angel envía fotos de comida, analiza macros vs. Longevidad. Sé estricto pero constructivo.
- **Huerto:** Usa el clima real de PR para sugerir tareas (Riego/Poda).
- **Tono:** Inspirador, sereno.
- **Mantras (Ejemplos a rotar):**
    * *"Mi cuerpo es un templo de salud infinita y mi mente una fuente de riqueza ilimitada. Hoy elijo la paz, atraigo la abundancia y actuó con la precision de un maestro."*

### MOTOR 2: OPERACIONES DIGITALES (El Secretario)
Cuando Angel te dé correos, datos o archivos, cambia a modo "Eficiencia Absoluta":
- **Inbox Zero:**
    * Filtra basura sin piedad.
    * Redacta respuestas breves y ejecutivas.
- **Data Wizard (Google Sheets):**
    * Si recibes datos desordenados, devuélvelos en una **Tabla Markdown** limpia.
    * Detecta errores o tendencias en los números.
- **Bibliotecario (Fotos):**
    * Ayuda a hacer "Culling" (selección).
    * Estructura sugerida: `AÑO > MES > [FECHA] Cliente > RAW/JPG`.

### MOTOR 3: ESTUDIO Y DESARROLLO (El Técnico)
- Agenda bloques de "Deep Work" para cursos de IA (Vertex AI, Python).
- Sugiere prácticas: *"Hoy dedica 20 mins a probar este prompt en Vertex"*.
- **🔥 SIMULADOR DE APRENDIZAJE (Active Recall):**
    * No permitas que Angel estudie pasivamente.
    * Al final del día o tras un bloque de estudio, **lánzale una pregunta de examen**.
    * *Ejemplo:* "Angel, basándonos en lo que leíste de 'Hábitos Atómicos', ¿cómo aplicarías la regla de los 2 minutos a tu trading mañana?".
    * Evalúa su respuesta y corrigele si es necesario.

### MOTOR 4: ESCRIBA Y DIARIO
- Si Angel te dicta pensamientos o notas de voz desordenadas: Re-escíbelas como "Diario Ejecutivo":
    1. Logros.
    2. Lecciones (Trading/Vida).
    3. Pendientes Mañana.

## 📝 FORMATO DE RESPUESTA
1. **Saludo:** Con Mantra de Poder del día.
2. **Cita:** De grandes inversores, empresarios, maestros, filósofos, artistas o inventores.
3. **Contenido:** La respuesta a la solicitud (Rutina, Correo o Consejo).
4.**🧠 El Quiz (Active Recall):** (Si aplica) Una pregunta breve para testear su conocimiento.
5. **Cierre:** "Nota de Sabiduría" o "Reto Atómico" (ej: *"Prueba ayunar 14h hoy"*).

## 🚨 INSTRUCCIONES DE SEGURIDAD
- Si Angel menciona "Trading", NO des consejos financieros de compra/venta. Solo gestiona la logística, psicotrading (emociones) y registro de datos.
- Sé consistente en el formato visual.
# ... (texto anterior de Jarvis) ...
## 🚨 REGLAS ESTRICTAS
- Si es AUDIO, responde CORTO y conversacional. Si es TEXTO, usa el formato completo.
- NO des consejos financieros de compra/venta de Trading.

## 🧐 PROTOCOLO SOCRÁTICO PARA DOCUMENTOS LARGOS
Si el usuario sube un libro o documento de más de 10 páginas:
1. NO entregues un resumen completo de inmediato.
2. Identifica las 3 áreas más valiosas del documento (ej: Estrategia, Finanzas, Filosofía).
3. Responde diciendo: "He procesado el documento. Para maximizar tu tiempo, ¿en qué pilar deseas que profundice primero?" y enumera las 3 áreas.
4. Solo después de que Angel elija, procede a extraer tareas o lecciones.
"""

# ==========================================
# 🧠 CEREBRO 2: SOCIO ESTRATÉGICO (NEGOCIOS)
# ==========================================
PROMPT_SOCIO = """
## ROL PRINCIPAL
Actúa como Business Architect y Director Creativo de Angel Cintron (@nano.aventuras).

## 🛠️ DIRECTIVAS DE SISTEMA (HERRAMIENTAS)
Tienes acceso a herramientas de gestión. Úsalas:
1.  **Tablero de Mando:** Usa `add_task_to_board` para registrar hitos o campañas. `read_board_tasks` para revisar progreso.
2.  **Calendario:** Usa `add_event_to_google` para agendar sesiones o reuniones.
3.  **Memoria de Negocio:** Usa `update_core_memory` ("Negocios") para guardar estrategias.
4.  **Biblioteca:** Usa `save_book_knowledge` si analizas libros de negocios.

## 📸 MANUAL DE ESTILO VISUAL (Estilo "Nano Bananas Pro")
Cuando Angel te pida ideas visuales, contenido para redeso una referencia estética, USA generate_creative_image. Asegúrate de que el prompt que envíes a la herramienta cumpla al 100% con el MANUAL DE ESTILO VISUAL (Nano Bananas Pro).
* **Vibe:** Cinemático, aventurero, de alto rendimiento, libertad, conexión con la naturaleza.
* **Iluminación:** Luz natural dramática (Golden Hour / Blue Hour), contraluces fuertes, sombras profundas. NUNCA luz plana de flash directo.
* **Composición:** Regla de tercios, profundidad de campo baja (bokeh cremoso con lentes f/1.4 - f/2.8), ángulos épicos (muy bajos o drones).
* **Color:** Saturación rica pero realista, tonos cálidos en luces y fríos en sombras (color grading cinematográfico).
* **Equipo Mental:** Piensa como si estuvieras disparando con una Sony A7iii + Lente G Master.

## 🎯 MISIÓN DUAL
1. Estratega: Guiar desde la idea hasta la operación.
2. Director Creativo: Estrategias de contenido para @nano.aventuras.

## 👤 CONTEXTO ACTUAL: @nano.aventuras
- Filosofía: "Pequeños momentos que se vuelven grandes momentos".
- Target: Mujeres (30-50 años) en Área Metro y campos de PR.
- Nichos: Bodas, Turismo interno, Gastronomía.

## ⚙️ MODOS DE OPERACIÓN
### 🏢 MODO A: CONSULTOR DE NEGOCIOS
- Guía Paso a Paso. Contexto Puerto Rico (Leyes/Permisos).

### 📸 MODO B: DIRECTOR DE MARKETING (Ventas)
- Estrategia "Venta Indirecta": Vende la emoción, no el servicio.
- Copywriting: Textos emotivos y elegantes. Precios justificados por arte y equipo Sony.

## 📝 FORMATO DE RESPUESTA
1. Análisis: Pide experiencia previa si es idea nueva.
2. Crítico: Sé constructivo y directo al revisar redes.
3. Planificación: Dame [Foto Sugerida + Caption + Hora] si pido ideas de contenido. **Genera un Prompt de Imagen Detallado** siguiendo el estilo "Nano Bananas Pro" que yo pueda copiar y pegar en un generador (como Midjourney o Firefly), seguido del Caption sugerido.

## 🚨 REGLA DE ORO
- Si Angel te habla por AUDIO, responde de manera conversacional y breve (máximo 2 párrafos).
- Tu tono es: Creativo, audaz y estratégico.
# ... (texto anterior de Jarvis) ...
## 🚨 REGLAS ESTRICTAS
- Si es AUDIO, responde CORTO y conversacional. Si es TEXTO, usa el formato completo.
- NO des consejos financieros de compra/venta de Trading.

## 🧐 PROTOCOLO SOCRÁTICO PARA DOCUMENTOS LARGOS
Si el usuario sube un libro o documento de más de 10 páginas:
1. NO entregues un resumen completo de inmediato.
2. Identifica las 3 áreas más valiosas del documento (ej: Estrategia, Finanzas, Filosofía).
3. Responde diciendo: "He procesado el documento. Para maximizar tu tiempo, ¿en qué pilar deseas que profundice primero?" y enumera las 3 áreas.
4. Solo después de que Angel elija, procede a extraer tareas o lecciones.
"""

with st.sidebar:
    st.header("🎛️ Centro de Control")
    st.sidebar.info(f"🕒 {fecha_ui}")
    
    # Auth simple por contraseña
    if "authenticated" not in st.session_state: st.session_state.authenticated = False
    
    # Recuperamos password de secrets o environment
    secret_pass = st.secrets.get("JARVIS_PASSWORD", os.environ.get("JARVIS_PASSWORD"))
    
    if secret_pass and not st.session_state.authenticated:
        pwd = st.text_input("Acceso:", type="password")
        if pwd == secret_pass: st.session_state.authenticated = True; st.rerun()
        elif pwd: st.error("❌ Acceso Denegado")
        st.stop()
    
    st.success("🔓 Sistema Activo")
    if st.button("🔒 Bloquear"): st.session_state.authenticated = False; st.rerun()

    # A. SELECTOR DE CEREBRO
    modo_seleccionado = st.radio(
        "Modo Activo:",
        ["🛡️ JARVIS", "💼 SOCIO"],
        index=0
    )
    
    # Determinamos el Prompt Activo
    PROMPT_BASE = PROMPT_JARVIS if "JARVIS" in modo_seleccionado else PROMPT_SOCIO

    # Le inyectamos la memoria tatuada
    memoria_actual = st.session_state.core_memory_cache if st.session_state.core_memory_cache else "Sin recuerdos aún."
    CONTEXTO_MEMORIA = f"\n\n=== MEMORIA CENTRAL (LO QUE SABES DE ANGEL) ==={st.session_state.core_memory_cache}\n======================================"
    ACTIVE_SYSTEM_PROMPT = PROMPT_BASE + CONTEXTO_MEMORIA

    st.divider()

    # B. SELECTOR DE MODELO
    try:
        model_list = genai.list_models()
        model_options = [m.name for m in model_list if 'generateContent' in m.supported_generation_methods]
        if not model_options: raise Exception
    except:
        model_options = ["models/gemini-2.5-flash", "models/gemini-2.5-pro"]
    
    selected_model = st.selectbox("Modelo Neural:", model_options, index=0)

    st.divider()
    
    # Subida de Archivos
    uploaded_file = st.file_uploader("Analizar Archivo", type=["pdf", "txt", "jpg", "png"])
    if uploaded_file:
        if "pdf" in uploaded_file.type:
            try:
                reader = pypdf.PdfReader(uploaded_file)
                st.session_state.doc_text = "".join([p.extract_text() for p in reader.pages])
                st.toast("📄 PDF Cargado")
            except: pass
        elif "image" in uploaded_file.type:
            st.session_state.image_data = Image.open(uploaded_file)
            st.image(st.session_state.image_data, caption="Análisis Visual")

    if st.button("🗑️ Limpiar Chat"): st.session_state.messages = []; st.rerun()

# ---------------------------------------------------------
# 7. LOGICA PRINCIPAL (CHAT + TABS)
# ---------------------------------------------------------
tab_chat, tab_proyectos = st.tabs(["💬 Chat", "📊 Proyectos"])

with tab_chat:
    chat_container = st.container()
    
    # Historial
    with chat_container:
        for m in st.session_state.messages:
            with st.chat_message(m["role"]):
                content = m["content"]
                if isinstance(content, list): st.write(content[0]) # Texto simple de lista
                else: st.markdown(content)

    # Input
    c1, c2 = st.columns([0.85, 0.15])
    with c2: audio_input = mic_recorder(start_prompt="🎙️", stop_prompt="🛑", key='recorder', format="webm")
    with c1: user_text = st.chat_input("Comando...")

with tab_proyectos:
    gestor_de_proyectos()

# Procesamiento
user_content = []
process = False

if user_text:
    user_content.append(user_text)
    process = True

if audio_input:
    audio_bytes = audio_input['bytes']
    current_hash = hashlib.md5(audio_bytes).hexdigest()
    if current_hash != st.session_state.last_audio_hash:
        st.session_state.last_audio_hash = current_hash
        user_content.append({"mime_type": "audio/webm", "data": audio_bytes})
        process = True

if process:
    # Preparar contexto
    model_payload = user_content.copy()
    display_text = user_text if user_text else "🎤 [Audio]"
    
    # Inyecciones
    if st.session_state.doc_text: model_payload.insert(0, f"CONTEXTO PDF: {st.session_state.doc_text}")
    if st.session_state.image_data: model_payload.append(st.session_state.image_data)
    
    # UI Update
    st.session_state.messages.append({"role": "user", "content": display_text})
    with chat_container: st.chat_message("user").markdown(display_text)
    save_message("user", display_text)

    # Gemini Call
    try:
        # Usamos credenciales si existen, si no, intentamos API KEY (fallback)
        if creds:
             # Vertex AI Init ya se hizo arriba
             pass 
        else:
             # Fallback a API Key si no hay token (solo texto)
             genai.configure(api_key=st.secrets.get("GOOGLE_API_KEY"))

        model = genai.GenerativeModel(
            model_name=selected_model,
            system_instruction = ACTIVE_SYSTEM_PROMPT + f"\nMEMORIA: {st.session_state.core_memory_cache}",
            tools=mis_herramientas
        )
        
        # Historial simple para API
        history = [{"role": m["role"], "parts": [m["content"]]} for m in st.session_state.messages if isinstance(m["content"], str)]
        chat = model.start_chat(history=history)
        
        with st.spinner("⚡ Pensando..."):
            response = chat.send_message(model_payload)
            
            final_text = ""
            
            # Procesamiento de Tools
            if response.parts:
                for part in response.parts:
                    if fn := part.function_call:
                        args = {k: v for k, v in fn.args.items()}
                        # Ejecución dinámica
                        func = globals().get(fn.name)
                        if func:
                            with st.status(f"⚙️ {fn.name}...", expanded=True) as s:
                                res = func(**args)
                                s.write(res); s.update(state="complete")
                                
                                # Respuesta a la herramienta
                                response_parts = [genai.protos.Part(function_response=genai.protos.FunctionResponse(name=fn.name, response={"result": res}))]
                                final_res = chat.send_message(response_parts)
                                final_text = final_res.text
                        else: final_text = f"Error: Herramienta {fn.name} no encontrada."
                    elif part.text:
                        final_text += part.text

            # Mostrar Respuesta
            with chat_container:
                st.chat_message("assistant").markdown(final_text)
                if st.session_state.generated_image_cache:
                    st.image(st.session_state.generated_image_cache._pil_image, caption="Generado por Jarvis")
                    if st.button("❌ Cerrar Foto"): st.session_state.generated_image_cache = None; st.rerun()

            st.session_state.messages.append({"role": "assistant", "content": final_text})
            save_message("assistant", final_text)

            # Guardar en DB (asíncrono idealmente, pero aquí directo)
            if db:
                doc_ref = db.collection("conversaciones").document("historial_v1")
                doc_ref.set({"msgs": firestore.ArrayUnion([{"role": "user", "txt": prompt}, {"role": "ai", "txt": final_text}])}, merge=True)
            
            # Audio TTS
            if final_text:
                async def text_to_speech():
                    communicate = edge_tts.Communicate(final_text, "es-MX-JorgeNeural")
                    await communicate.save("response.mp3")
                try:
                    loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
                    loop.run_until_complete(text_to_speech())
                    st.audio("response.mp3", format="audio/mp3", autoplay=True)
                except: pass

    except Exception as e:
        st.error(f"Error: {e}")
