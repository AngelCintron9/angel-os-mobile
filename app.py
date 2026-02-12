import streamlit as st
import google.generativeai as genai
from google.cloud import firestore
import os
import time
import json
from PIL import Image
import pypdf
from gtts import gTTS
import io
from streamlit_mic_recorder import mic_recorder
from google.oauth2 import service_account
from googleapiclient.discovery import build
import hashlib
import asyncio
import edge_tts
import firebase_admin
from firebase_admin import credentials, firestore


# ---------------------------------------------------------
# 1. CONFIGURACIÓN INICIAL
# ---------------------------------------------------------
st.set_page_config(page_title="Angel OS - Jarvis", page_icon="🎙️", layout="wide")

# --- DIAGNÓSTICO EN VIVO ---
status = st.empty() # Creamos un espacio vacío para mensajes
status.info("🚀 Iniciando Angel OS...")
time.sleep(0.5)

status.info("📂 Cargando librerías...")
# Aquí van tus imports pesados si quedaron algunos...

status.info("🔥 Conectando a la Base de Datos...")
# Aquí va tu código de conexión a Firebase...
# (Si se queda aquí, es culpa de las credenciales)

# Si pasa todo, borramos el mensaje
status.empty()

# Inicializar Variables de Estado
if "messages" not in st.session_state: st.session_state.messages = []
if "last_audio_hash" not in st.session_state: st.session_state.last_audio_hash = None
if "doc_text" not in st.session_state: st.session_state.doc_text = ""
if "image_data" not in st.session_state: st.session_state.image_data = None
if "generated_image_cache" not in st.session_state:
    st.session_state.generated_image_cache = None # Aquí guardaremos la obra de arte

# ---------------------------------------------------------
# 2. FUNCIONES DE CONEXIÓN (MODO DETECTIVE + SECRETS)
# ---------------------------------------------------------
import google.auth
from googleapiclient.discovery import build
import os

# Scopes: Permisos necesarios para leer/escribir en Calendar
SCOPES = ['https://www.googleapis.com/auth/calendar']

def get_secret(key_name):
    """Obtiene secretos de Streamlit o Variables de Entorno"""
    if key_name in st.secrets: return st.secrets[key_name]
    return os.environ.get(key_name)

def get_google_credentials():
    """Conexión Nativa para Google Cloud (ADC) con depuración visual"""
    # st.write("🕵️‍♂️ Iniciando autenticación...") # Descomentar para ver logs en pantalla
    try:
        # La magia de Google: Busca automáticamente tus credenciales de la terminal
        creds, project = google.auth.default(scopes=SCOPES)
        return creds
    except Exception as e:
        st.error(f"⚠️ Error de Autenticación Cloud: {e}")
        return None

def test_calendar_connection():
    """Prueba simple para ver si podemos hablar con Google"""
    creds = get_google_credentials()
    if not creds: 
        st.error("❌ No se encontraron credenciales.")
        return
    
    try:
        service = build('calendar', 'v3', credentials=creds)
        # Intentamos listar los calendarios (operación de lectura)
        events = service.calendarList().list().execute()
        st.success("🎉 ¡CONEXIÓN EXITOSA AL CALENDARIO! Jarvis tiene permiso real.")
        st.write(f"📅 Calendarios encontrados: {len(events.get('items', []))}")
    except Exception as e:
        st.error(f"❌ Error conectando al API de Calendario:\n{e}")
        st.warning("💡 PISTA: Si el error es 403, falta habilitar la API o dar permisos en 'gcloud auth'.")
        
# ---------------------------------------------------------
# 3. FUNCIONES DE GESTIÓN DE PROYECTOS (TABLAS)
# ---------------------------------------------------------
import pandas as pd
import time # Añadimos time para una pausa visual al guardar

def gestor_de_proyectos():
    st.header("📊 Tablero de Mando")

    try:
        docs = db.collection('proyectos').stream()
        items = [{'id': doc.id, **doc.to_dict()} for doc in docs]
    except Exception as e:
        st.error(f"Error conectando a la base de datos: {e}")
        items = []

    # 2. Crear DataFrame (Tabla)
    if items:
        df = pd.DataFrame(items)
        cols = ['Tarea', 'Estado', 'Prioridad', 'Fecha', 'id']
        for col in cols:
            if col not in df.columns: df[col] = None # Usamos None en vez de ""
        
        df = df[cols]
        # 💡 LA MAGIA: Convertimos el texto a Fecha Real para Streamlit
        df['Fecha'] = pd.to_datetime(df['Fecha'], errors='coerce')
    else:
        df = pd.DataFrame(columns=['Tarea', 'Estado', 'Prioridad', 'Fecha', 'id'])
        # A la tabla vacía también le decimos que la columna será fecha
        df['Fecha'] = pd.to_datetime(df['Fecha'], errors='coerce') 

    # 3. EL EDITOR MÁGICO
    edited_df = st.data_editor(
        df,
        num_rows="dynamic",
        column_config={
            "Estado": st.column_config.SelectboxColumn("Estado", options=["🚀 Por hacer", "⚙️ En Progreso", "✅ Completado"], required=True),
            "Prioridad": st.column_config.SelectboxColumn("Prioridad", options=["🔥 Alta", "🔵 Media", "🟢 Baja"], required=True),
            "Fecha": st.column_config.DateColumn("Fecha Límite"),
            "id": st.column_config.Column(disabled=True),
        },
        hide_index=True,
        key="editor_proyectos"
    )

    # 4. Botón de Guardado
    if st.button("💾 Guardar Cambios en la Nube"):
        with st.spinner("Sincronizando con Firestore..."):
            try:
                # 💡 TRADUCCIÓN INVERSA: Antes de guardar, volvemos a pasar la fecha a texto o vacío
                edited_df['Fecha'] = edited_df['Fecha'].apply(lambda x: x.strftime('%Y-%m-%d') if pd.notnull(x) else None)
                
                collection_ref = db.collection('proyectos')
                records = edited_df.to_dict(orient='records')
                
                for record in records:
                    doc_id = record.pop('id', None)
                    
                    if doc_id and len(str(doc_id)) > 5:
                        # Si tiene ID, actualiza
                        collection_ref.document(doc_id).set(record)
                    else:
                        # Si NO tiene ID, crea uno nuevo (solo si la tarea no está en blanco)
                        if str(record.get('Tarea', '')).strip() != "":
                            collection_ref.add(record)
                
                st.success("✅ ¡Tablero actualizado!")
                time.sleep(1) # Pausa para que veas el mensaje verde
                st.rerun() # Recargamos para limpiar
            except Exception as e:
                st.error(f"Error al guardar: {e}")
                
# ---------------------------------------------------------
# 4. BASE DE DATOS (FIRESTORE) - OPTIMIZADO
# ---------------------------------------------------------

# A. Función de Conexión con CACHÉ (El secreto para que no falle el arranque)
@st.cache_resource
def get_firestore_connection():
    try:
        creds = get_google_credentials() # Tu función actual
        if creds:
            # Conectamos una sola vez y guardamos la conexión en memoria
            return firestore.Client(credentials=creds, project="jarvis-ia-v1", database="firestore")
        return None
    except Exception as e:
        print(f"Error interno Firestore: {e}")
        return None

# B. Inicialización Rápida
db = get_firestore_connection()
DOCUMENT_ID = "memoria_jarvis_v2"
doc_ref = None

# C. Configuración de Referencia
if db:
    try:
        doc_ref = db.collection("conversaciones").document(DOCUMENT_ID)
    except Exception as e:
        st.error(f"Error conectando colección: {e}")
else:
    st.warning("⚠️ No hay credenciales. Base de datos apagada (Modo Offline).")

def save_message(role, content):
    """Guarda el mensaje en Firestore si está conectado"""
    if doc_ref:
        try:
            # Si es imagen o algo complejo, guardamos un placeholder
            text_to_save = content if isinstance(content, str) else "[Contenido Multimodal]"
            doc_ref.set({
                "messages": firestore.ArrayUnion([{"role": role, "content": text_to_save, "timestamp": time.time()}])
            }, merge=True)
        except Exception as e:
            print(f"No se pudo guardar en nube: {e}")

# --- BÓVEDA DE MEMORIA CENTRAL ---
if "core_memory_cache" not in st.session_state: 
    st.session_state.core_memory_cache = None # Empezamos vacío

def load_core_memory():
    """Lee la bóveda de Firestore una sola vez y la formatea como texto"""
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
        print(f"Error cargando memoria central: {e}")
        return ""

# Cargamos a la RAM solo si no se ha cargado antes
if st.session_state.core_memory_cache is None:
    st.session_state.core_memory_cache = load_core_memory()

# ---------------------------------------------------------
# 5. HERRAMIENTAS (CALENDARIO)
# ---------------------------------------------------------
CALENDAR_ID = "angelyavielcintron77@gmail.com"

# ---------------------------------------------------------
# 5. HERRAMIENTAS (TOOLS)
# ---------------------------------------------------------
from datetime import datetime, timezone, timedelta

# --- RELOJ GLOBAL PARA LA INTERFAZ --- (Sin espacios a la izquierda)
zona_pr = timezone(timedelta(hours=-4))
fecha_ui = datetime.now(zona_pr).strftime("%A, %d de %B de %Y - %I:%M %p")

# --- HERRAMIENTA COGNITIVA PARA JARVIS ---
def get_current_time():
    """
    Reloj Interno del Sistema.
    Devuelve la fecha y hora exacta actual en Puerto Rico. 
    """
    zona_pr_jarvis = timezone(timedelta(hours=-4))
    fecha_exacta = datetime.now(zona_pr_jarvis).strftime("%A, %d de %B de %Y - %I:%M:%S %p")
    return f"La fecha y hora actual en el sistema es: {fecha_exacta}"

def update_core_memory(hecho, categoria="General"):
    """
    Bóveda de Memoria a Largo Plazo.
    Úsala proactivamente para guardar datos vitales, preferencias o metas de Angel.
    Args:
        hecho: El dato exacto a recordar.
        categoria: Clasificación (ej. "Negocios", "Personal", "Preferencias").
    """
    if not db: return "❌ Error: Base de datos no conectada."
    
    try:
        doc_ref = db.collection('memoria_central').document(categoria)
        doc_ref.set({
            "recuerdos": firestore.ArrayUnion([hecho]),
            "ultima_actualizacion": time.time()
        }, merge=True)
        
        # Actualizamos la caché en tiempo real para que Jarvis lo sepa ya mismo
        if st.session_state.core_memory_cache is None:
            st.session_state.core_memory_cache = ""
        st.session_state.core_memory_cache += f"\n- [{categoria}]: {hecho}"
        
        return f"🧠 Recuerdo tatuado en la bóveda [{categoria}]: '{hecho}'"
    except Exception as e:
        return f"❌ Error al guardar en bóveda: {str(e)}"

def add_event_to_google(summary, start_time, duration_minutes=60):
    """
    Agendador Real.
    Crea eventos en Google Calendar usando las credenciales nativas del sistema.
    Args:
        summary: Título del evento.
        start_time: Fecha y hora en formato ISO (ej: '2026-02-10T17:00:00').
        duration_minutes: Duración en minutos (default 60).
    """
    # 1. Obtenemos las credenciales (Usando la función nueva que SÍ funciona)
    creds = get_google_credentials()
    
    if not creds:
        return "❌ Error: No tengo credenciales válidas para acceder al calendario."

    try:
        # 2. Conectamos con Google
        service = build('calendar', 'v3', credentials=creds)
        
        # 3. Calculamos horas (Parseo robusto)
        try:
            # Intentamos leer el formato que manda Gemini
            if "T" in start_time:
                start_dt = datetime.fromisoformat(start_time)
            else:
                # A veces manda solo fecha, asumimos 9am
                start_dt = datetime.fromisoformat(f"{start_time}T09:00:00")
        except:
            return f"❌ Formato de fecha no entendido: {start_time}"

        end_dt = start_dt + timedelta(minutes=duration_minutes)

        # 4. Creamos el objeto del evento
        event = {
            'summary': summary,
            'start': {
                'dateTime': start_dt.isoformat(),
                'timeZone': 'America/Puerto_Rico',
            },
            'end': {
                'dateTime': end_dt.isoformat(),
                'timeZone': 'America/Puerto_Rico',
            },
        }

        # 5. ¡ENVIAMOS A LA NUBE!
        event_result = service.events().insert(calendarId='primary', body=event).execute()
        
        return f"✅ Evento creado con éxito: {summary} ({start_dt.strftime('%H:%M')}). Link: {event_result.get('htmlLink')}"

    except Exception as e:
        return f"❌ Error de Google Calendar: {str(e)}"

def add_task_to_board(tarea, estado="🚀 Por hacer", prioridad="🔵 Media", fecha="", **kwargs):
    """
    Gestor de Tareas.
    Añade una nueva misión o tarea al Tablero de Mando (Base de datos).
    Args:
        tarea: Descripción corta de la tarea.
        estado: Puede ser "🚀 Por hacer", "⚙️ En Progreso", o "✅ Completado".
        prioridad: Puede ser "🔥 Alta", "🔵 Media", o "🟢 Baja".
        fecha: Fecha límite opcional en formato YYYY-MM-DD.
    """
    if not db:
        return "❌ Error: La base de datos no está conectada."
    
    try:
        nueva_tarea = {
            "Tarea": tarea,
            "Estado": estado,
            "Prioridad": prioridad,
            "Fecha": fecha
        }
        # Guardar en la colección 'proyectos' de Firestore
        db.collection('proyectos').add(nueva_tarea)
        return f"✅ Misión añadida al tablero: '{tarea}' (Prioridad: {prioridad})"
    except Exception as e:
        return f"❌ Error guardando la tarea: {str(e)}"

def read_board_tasks(filtro_estado=""):
    """
    Ojo Analítico del Tablero.
    Lee las tareas actuales en el Tablero de Mando (Firestore).
    Args:
        filtro_estado: (Opcional) Filtrar por "🚀 Por hacer", "⚙️ En Progreso", o "✅ Completado". 
                       Si se deja vacío, lee todas las tareas.
    """
    if not db:
        return "❌ Error: Base de datos no conectada."
    
    try:
        docs = db.collection('proyectos').stream()
        tareas = []
        
        for doc in docs:
            data = doc.to_dict()
            estado_actual = data.get("Estado", "")
            
            # Si Jarvis usa un filtro, ignoramos las tareas que no coincidan
            if filtro_estado and filtro_estado not in estado_actual:
                continue
                
            tarea_str = f"- Tarea: '{data.get('Tarea', 'Sin título')}' | Prioridad: {data.get('Prioridad', 'N/A')} | Estado: {estado_actual} | Fecha Límite: {data.get('Fecha', 'Sin fecha')}"
            tareas.append(tarea_str)
        
        if not tareas:
            return f"El tablero está vacío o no hay tareas bajo el filtro: '{filtro_estado}'."
            
        return "📋 TAREAS ENCONTRADAS EN EL TABLERO:\n" + "\n".join(tareas)
    except Exception as e:
        return f"❌ Error leyendo el tablero: {str(e)}"

def save_book_knowledge(titulo, aprendizajes_clave):
    """
    Guarda el resumen de un libro en la Biblioteca Permanente de Firestore.
    Args:
        titulo: Título del libro.
        aprendizajes_clave: Resumen de los puntos más importantes (texto).
    """
    if not db: return "❌ Error DB"
    
    try:
        # Crea un documento nuevo en la colección 'biblioteca'
        db.collection('biblioteca').document(titulo).set({
            "resumen": aprendizajes_clave,
            "fecha_lectura": datetime.now().strftime("%Y-%m-%d")
        })
        return f"📚 Libro '{titulo}' guardado en la Biblioteca Permanente."
    except Exception as e:
        return f"❌ Error guardando libro: {str(e)}"

def generate_creative_image(prompt_visual):
    """
    Motor de Arte Digital (Nano Banana / Imagen 3 FAST).
    OPTIMIZADO: Carga las librerías SOLO cuando se necesitan (Lazy Import).
    """
    
    # 1. IMPORTACIÓN TÁCTICA (Aquí es donde ganamos velocidad de inicio)
    # Al ponerlo aquí dentro, la App no se traba al arrancar.
    import vertexai
    from vertexai.preview.vision_models import ImageGenerationModel

    print(f"🎨 Iniciando generación con Imagen 3 Fast: {prompt_visual[:50]}...")
    
    try:
        # 2. Configuración de Región
        vertexai.init(location="us-central1")
        
        # 3. Cargamos el modelo
        model = ImageGenerationModel.from_pretrained("imagen-3.0-fast-generate-001")
        
        with st.spinner("⚡ Revelando fotografía a alta velocidad..."):
            images = model.generate_images(
                prompt=prompt_visual,
                number_of_images=1,
                language="es",
                aspect_ratio="16:9",
                safety_filter_level="block_some", 
                person_generation="allow_adult"
            )
            
            if images:
                st.session_state.generated_image_cache = images[0]
                return "✅ Imagen revelada exitosamente."
            else:
                return "⚠️ El motor no devolvió datos."

    except Exception as e:
        return f"❌ Error Técnico: {str(e)}"
        
        # PLAN B: Si falla, devolvemos el Prompt
        return (
            f"❌ **Error Técnico:** {error_msg}\n\n"
            f"🛡️ **PLAN DE CONTINGENCIA:** Prompt manual:\n"
            f"```text\n{prompt_visual}\n```"
        )

# 1. El Directorio de Herramientas (Añade esto debajo de tus funciones)
mapa_herramientas = {
    "add_event_to_google": add_event_to_google,
    "add_task_to_board": add_task_to_board,
    "get_current_time": get_current_time,
    "update_core_memory": update_core_memory,
    "read_board_tasks": read_board_tasks,
    "save_book_knowledge": save_book_knowledge,
    "generate_creative_image": generate_creative_image
}

mis_herramientas = list(mapa_herramientas.values())

# ---------------------------------------------------------
# 6. PERSONALIDADES (PROMPTS)
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

# ---------------------------------------------------------
# 7. INTERFAZ: SIDEBAR (CONFIGURACIÓN)
# ---------------------------------------------------------
with st.sidebar:
    st.header("🎛️ Centro de Control")
    st.sidebar.info(f"🕒 Reloj del Sistema: {fecha_ui}")

    # --- SISTEMA DE AUTENTICACIÓN TÁCTICA ---
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    secret_pass = get_secret("JARVIS_PASSWORD")
    
    if secret_pass:
        # Si NO estamos autenticados, mostramos la caja de texto
        if not st.session_state.authenticated:
            pwd = st.text_input("Identificación Requerida:", type="password")
            
            if pwd == secret_pass:
                st.session_state.authenticated = True
                st.rerun() # Recargamos para desaparecer la caja
            elif pwd != "":
                st.error("❌ Credenciales incorrectas")
                
            # Bloqueamos el resto de la app si no hay acceso
            if not st.session_state.authenticated:
                st.warning("🔒 Terminal Bloqueada"); st.stop()
        
        # Si SÍ estamos autenticados, mostramos el botón de bloqueo rápido
        else:
            st.success("🔓 Acceso Concedido: Bienvenido Arquitecto")
            if st.button("🔒 Bloquear Terminal", type="primary"):
                st.session_state.authenticated = False
                st.rerun() # Recargamos para volver a pedir la clave

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

    # C. SUBIDA MULTIMODAL (ARCHIVOS)
    uploaded_file = st.file_uploader("Analizar Archivo", type=["pdf", "txt", "jpg", "png"])
    
    if st.session_state.doc_text:
        st.info("📂 Libro en Memoria (Modo Silencioso)")
        if st.button("❌ Olvidar Libro", key="btn_olvidar"):
            st.session_state.doc_text = ""
            st.session_state.image_data = None
            st.rerun()

    if uploaded_file:
        file_type = uploaded_file.type
        if "pdf" in file_type:
            try:
                reader = pypdf.PdfReader(uploaded_file)
                text = ""
                for page in reader.pages: text += page.extract_text()
                st.session_state.doc_text = text
                st.success("📄 PDF Leído")
            except: st.error("Error leyendo PDF")
        elif "image" in file_type:
            st.session_state.image_data = Image.open(uploaded_file)
            st.image(st.session_state.image_data, caption="Vista Previa", use_container_width=True)

    if st.button("🗑️ Reiniciar Cerebro"):
        st.session_state.messages = []
        st.session_state.doc_text = ""
        st.session_state.image_data = None
        st.session_state.last_audio_id = None
        st.rerun()

# ==========================================
# 8. INTERFAZ PRINCIPAL CON PESTAÑAS
# ==========================================

# Crear las pestañas
tab_chat, tab_proyectos = st.tabs(["💬 Chat con Jarvis", "📊 Gestión de Proyectos"])

# --- PESTAÑA 1: CHAT (Toda tu lógica actual va aquí) ---
with tab_chat:
    st.subheader("Cerebro Digital")

    # ---------------------------------------------------------
    # CHAT VISUAL (Pegado aquí adentro)
    # ---------------------------------------------------------
    chat_container = st.container()

    # Mostrar historial visualmente
    with chat_container:
        if not st.session_state.messages:
            st.info(f"Sistema en línea: {modo_seleccionado}")
        
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                # Si el contenido es una lista (multimodal)...
                if isinstance(message["content"], list):
                    text_part = next((p for p in message["content"] if isinstance(p, str)), None)
                    if text_part:
                        st.markdown(text_part)
                    else:
                        st.markdown("🎤 *[Audio Enviado]*")
                else:
                    st.markdown(message["content"])
    
    # 2. Área de Input (Micrófono y Texto)
    st.divider()
    c1, c2 = st.columns([0.85, 0.15])

    with c2:
        # El micrófono a la derecha (Columna pequeña)
        audio_input = mic_recorder(
            start_prompt="🎙️", 
            stop_prompt="🛑", 
            key='recorder', 
            format="webm" 
        )

    with c1:
        # El texto a la izquierda (Columna grande)
        user_text = st.chat_input("Escribe tu comando aquí...", key="chat_principal_unico")

# --- PESTAÑA 2: PROYECTOS ---
with tab_proyectos:
    gestor_de_proyectos()

# --- LÓGICA DE PROCESAMIENTO (CEREBRO) ---
process_interaction = False
user_content = [] # La lista que enviaremos a Gemini

# 1. Detectar si hay texto nuevo
if user_text:
    user_content.append(user_text)
    process_interaction = True

# 2. Detectar si hay audio nuevo (CON HASH CHECK)
if audio_input:
    # A. Extraemos los bytes (Corrección del KeyError)
    audio_bytes = audio_input['bytes'] 
    
    # B. Calculamos la huella digital (Hash MD5)
    current_hash = hashlib.md5(audio_bytes).hexdigest()
    
    # C. Comparamos con la última huella guardada
    if current_hash != st.session_state.last_audio_hash:
        # ¡Es un audio nuevo! Actualizamos la huella y procesamos
        st.session_state.last_audio_hash = current_hash
        
        # Empaquetamos el audio para Gemini
        audio_blob = {
            "mime_type": "audio/webm",
            "data": audio_bytes
        }
        user_content.append(audio_blob)
        process_interaction = True
        st.toast("👂 Audio Nuevo Recibido")
    else:
        # Es el mismo audio de antes (Ghost Audio), lo ignoramos silenciosamente
        pass

# --- LÓGICA DE PROCESAMIENTO MULTIMODAL MEJORADA ---
if process_interaction:
    # 1. Creamos una copia para el Modelo (Payload) y dejamos user_content limpio para la UI
    model_payload = user_content.copy()

    # A. Inyectar Contexto de Documentos (Solo al Payload del modelo)
    if st.session_state.doc_text:
        # 💡 CAMBIO CLAVE: Instrucción Pasiva (para que no resuma siempre)
        instruccion_doc = (
            f"\n\n[CONTEXTO DE FONDO - NO RESUMIR A MENOS QUE SE PIDA]:\n"
            f"El usuario tiene este documento cargado en RAM.\n"
            f"Úsalo SOLO si la pregunta actual lo requiere explícitamente.\n"
            f"CONTENIDO:\n{st.session_state.doc_text}"
        )
        
        # Insertamos en la copia que va para Gemini
        if model_payload and isinstance(model_payload[0], str):
            model_payload[0] += instruccion_doc
        else:
            model_payload.insert(0, instruccion_doc)
    
    # B. Inyectar Imagen (Solo al Payload)
    if st.session_state.image_data:
        model_payload.append(st.session_state.image_data)
        st.toast("👁️ Analizando imagen...")

    # C. Mostrar mensaje LIMPIO en pantalla (Sin el texto del libro)
    display_text = user_text if user_text else "🎤 *[Mensaje de Voz]*"
    if st.session_state.doc_text: display_text += " 📎 *[Contexto Activo]*"
    
    with chat_container:
        st.chat_message("user").markdown(display_text)
    
    # Guardamos en historial la versión LIMPIA
    st.session_state.messages.append({"role": "user", "content": display_text})
    save_message("user", display_text)

   # D. INVOCAR A GEMINI
    try:
        # 1. Configuración del Modelo
        model = genai.GenerativeModel(
            model_name=selected_model,
            system_instruction=ACTIVE_SYSTEM_PROMPT, 
            tools=mis_herramientas
        )

        # 2. Preparar Historial (Solo texto para evitar errores de serialización)
        history_gemini = []
        for m in st.session_state.messages[:-1]:
            content_str = ""
            if isinstance(m["content"], list):
                for p in m["content"]:
                    if isinstance(p, str): content_str += p
            elif isinstance(m["content"], str):
                content_str = m["content"]
            
            if content_str:
                history_gemini.append({"role": "user" if m["role"] == "user" else "model", "parts": [content_str]})

        # 3. Iniciar Chat
        chat = model.start_chat(history=history_gemini)
        
        with st.spinner("⚡ Procesando..."):
            response = chat.send_message(user_content)
            
            final_text = ""
            function_handled = False
            
            # 4. ANÁLISIS DE LA RESPUESTA (Lógica Blindada v2)
            if response.parts:
                for part in response.parts:
                    
                    # CASO A: Es una llamada a función (Calendar)
                    if fn := part.function_call:
                        function_handled = True
                        args = {key: val for key, val in fn.args.items()}

                        with st.status(f"⚙️ Ejecutando protocolo: {fn.name}...", expanded=True) as s:
                            s.write(f"📦 Datos extraídos: {args}")
                            
                            # EL DESPACHADOR DINÁMICO
                            if fn.name in mapa_herramientas:
                                # 1. Busca la función en el diccionario y la ejecuta con los argumentos
                                funcion_a_ejecutar = mapa_herramientas[fn.name]
                                res = funcion_a_ejecutar(**args)
                                
                                s.write(f"Resultado: {res}")
                                s.update(label="✅ Operación completada", state="complete")
                                
                                # 2. Devolvemos el resultado a Gemini
                                try:
                                    response_parts = [
                                        genai.protos.Part(
                                            function_response=genai.protos.FunctionResponse(
                                                name=fn.name, # Nombre dinámico
                                                response={"result": res}
                                            )
                                        )
                                    ]
                                    final_response = chat.send_message(response_parts)
                                    final_text = final_response.text
                                except Exception as e:
                                    final_text = f"✅ Protocolo ejecutado, pero hubo un error en la síntesis verbal: {e}"
                            else:
                                s.update(label="❌ Herramienta desconocida", state="error")
                                final_text = f"⚠️ Intenté usar una herramienta inexistente: {fn.name}"
       
                    # CASO B: Es texto normal (Respuesta directa)
                    elif part.text:
                        final_text += part.text

            # Si por alguna razón la respuesta quedó vacía
            if not final_text and not function_handled:
                final_text = "⚠️ Gemini recibió la orden, pero envió una respuesta vacía."

       # F. Mostrar Respuesta Final, Imagen y Audio
        with chat_container:
            with st.chat_message("assistant"):
                # 1. Mostrar Texto
                st.markdown(final_text)

                # 2. --- VISUALIZADOR DE IMÁGENES (PERSISTENTE) ---
                if st.session_state.generated_image_cache:
                    st.toast("📸 Fotografía revelada")
                    
                    # Mostramos la imagen
                    st.image(
                        st.session_state.generated_image_cache._pil_image, 
                        caption="Generado por Angel OS | Estilo Nano Bananas Pro", 
                        use_column_width=True
                    )
                    
                    # Botón MANUAL para cerrar la foto (no automático)
                    if st.button("❌ Cerrar Fotografía", key="close_img_btn"):
                        st.session_state.generated_image_cache = None
                        st.rerun()
                
                # 3. --- SISTEMA DE VOZ NEURAL (EDGE-TTS) ---
                if final_text:
                    try:
                        VOZ_NEURAL = "es-MX-JorgeNeural"
                        archivo_audio = "respuesta_jarvis.mp3"
                        
                        async def generar_voz():
                            communicate = edge_tts.Communicate(final_text, VOZ_NEURAL)
                            await communicate.save(archivo_audio)

                        try:
                            loop = asyncio.get_event_loop()
                        except RuntimeError:
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                        
                        if loop.is_running():
                            asyncio.run_coroutine_threadsafe(generar_voz(), loop)
                        else:
                            loop.run_until_complete(generar_voz())
                        
                        st.audio(archivo_audio, format='audio/mp3')
                        
                    except Exception as e:
                        print(f"⚠️ Error voz neural: {e}")

        # --- GUARDADO Y LIMPIEZA ---
        
        # 1. Guardar en Historial
        st.session_state.messages.append({"role": "assistant", "content": final_text})
        save_message("assistant", final_text)
        
        # 2. Lógica de Limpieza (SOLO PARA INPUTS, NO PARA LA FOTO)
        should_rerun = False
        
        # Limpiamos inputs de imagen del usuario (lo que tú subes)
        if st.session_state.image_data:
            st.session_state.image_data = None
            should_rerun = True
        
        # Solo recargamos si hubo limpieza de tus archivos subidos
        if should_rerun:
            time.sleep(0.5)
            st.rerun()

    except Exception as e:
        # Imprimimos el error completo para debuggear si vuelve a pasar
        import traceback
        st.error(f"❌ Error Crítico: {e}")
        with st.expander("Ver detalles técnicos"):
            st.code(traceback.format_exc())

if st.button("🧪 PROBAR CONEXIÓN CALENDARIO", key="boton_prueba_clon"):

    test_calendar_connection()


