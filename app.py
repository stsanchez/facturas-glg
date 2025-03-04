import os
import openai
import pdfplumber
import gspread
from google.oauth2.service_account import Credentials
import time
import json
from tenacity import retry, stop_after_attempt, wait_exponential
from flask import Flask, render_template, request, redirect, url_for, send_from_directory
from dotenv import load_dotenv
from io import BytesIO
import logging

app = Flask(__name__, static_folder='static')

# Carga las variables de entorno desde .env
load_dotenv()

# Obtén las variables de entorno
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GOOGLE_SHEETS_SCOPES = os.getenv("GOOGLE_SHEETS_SCOPES").split(",")  # Los scopes son una lista
GOOGLE_SHEETS_CREDENTIALS = os.getenv("GOOGLE_SHEETS_CREDENTIALS")  # La variable JSON en formato string
GOOGLE_SHEETS_URL = os.getenv("GOOGLE_SHEETS_URL")

# Configura tu clave API de OpenAI
openai.api_key = OPENAI_API_KEY

# Obtener las credenciales desde la variable de entorno
credentials_info = json.loads(GOOGLE_SHEETS_CREDENTIALS)

# Crear credenciales desde el JSON en la variable de entorno
credentials = Credentials.from_service_account_info(credentials_info, scopes=GOOGLE_SHEETS_SCOPES)

# Autorizar acceso a Google Sheets
gc = gspread.authorize(credentials)

# Abre la hoja de cálculo por URL
spreadsheet = gc.open_by_url(GOOGLE_SHEETS_URL)
worksheet = spreadsheet.sheet1  # Usamos la primera hoja

def extract_text_from_pdf(pdf_file):
    """Extrae texto de un archivo PDF en memoria usando pdfplumber."""
    with pdfplumber.open(BytesIO(pdf_file.read())) as pdf:
        text = "\n".join(page.extract_text() for page in pdf.pages if page.extract_text())
    return text

from openai import OpenAI

# Configura el cliente de OpenAI
client = OpenAI(api_key=OPENAI_API_KEY)


def extract_invoice_data_using_gpt(pdf_text):
    """
    Usa la API de OpenAI para analizar el texto del PDF y extraer los datos de la factura.
    Maneja reintentos manualmente en caso de errores.
    """
    max_attempts = 3  # Número máximo de intentos
    wait_time = 4  # Tiempo de espera inicial entre intentos (en segundos)

    for attempt in range(max_attempts):
        try:
            logging.info(f"Intento {attempt + 1} de {max_attempts}...")
            prompt = f"""
            Extrae los siguientes campos del texto de una factura. Si un campo no existe, responde con un espacio vacío:
            1. Importe Neto Gravado en dólares (USD)
            2. Importe Neto Gravado en pesos ($)
            3. IVA 21% en dólares (USD)
            4. IVA 21% en pesos ($)
            5. Importe Otros Tributos en dólares (USD)
            6. Importe Otros Tributos en pesos ($)
            7. Importe Total en dólares (USD)
            8. Importe Total en pesos ($)
            9. Valor Total en Pesos (si la factura está en dólares y este campo existe, extraerlo; si no existe, usar el Importe Total)
            10. Número de Comprobante
            11. Fecha de Emisión
            12. CUIT del receptor (excluir "30711391963" y eliminar guiones)
            13. Razón Social del receptor (excluir "GLOBAL LOGISTICS")

            Devuelve el resultado estrictamente en formato JSON válido, sin texto adicional ni explicaciones. Por ejemplo:
            {{
                "Importe Neto Gravado (USD)": "",
                "Importe Neto Gravado ($)": "",
                "IVA 21% (USD)": "",
                "IVA 21% ($)": "",
                "Importe Otros Tributos (USD)": "",
                "Importe Otros Tributos ($)": "",
                "Importe Total (USD)": "",
                "Importe Total ($)": "",
                "Valor Total en Pesos": "",
                "Número de Comprobante": "",
                "Fecha de Emisión": "",
                "CUIT del receptor": "",
                "Razón Social del receptor": ""
            }}

            Aquí está el texto de la factura:
            {pdf_text}
            """
            logging.info("Enviando solicitud a OpenAI...")
            response = client.chat.completions.create(
                model="gpt-4",  # Cambia a "gpt-3.5-turbo" si es necesario 
                
                messages=[
                    {"role": "system", "content": "Eres un experto en análisis de texto y procesamiento de facturas."},
                    {"role": "user", "content": prompt}
                ]
            )
            logging.info("Respuesta de OpenAI recibida.")
            content = response.choices[0].message.content
            logging.info(f"Contenido de la respuesta: {content}")

            # Decodificar el JSON
            logging.info("Intentando decodificar JSON...")
            extracted_data = json.loads(content)
            logging.info("JSON decodificado correctamente.")
            return extracted_data  # Si tiene éxito, retorna los datos

        except openai.RateLimitError as e:
            logging.error(f"RateLimitError: {e}. Reintentando en {wait_time} segundos...")
            time.sleep(wait_time)
            wait_time *= 2  # Aumenta el tiempo de espera exponencialmente

        except openai.APIError as e:
            logging.error(f"APIError: {e}")
            raise  # No reintentar en caso de error de API

        except json.JSONDecodeError as e:
            logging.error(f"Error al decodificar JSON: {e}")
            logging.error(f"Respuesta del modelo: {content}")
            raise  # No reintentar en caso de error de decodificación de JSON

        except Exception as e:
            logging.error(f"Error inesperado: {e}")
            if attempt == max_attempts - 1:  # Si es el último intento, relanza la excepción
                raise
            logging.info(f"Reintentando en {wait_time} segundos...")
            time.sleep(wait_time)
            wait_time *= 2  # Aumenta el tiempo de espera exponencialmente

    logging.error("Se agotaron los intentos.")
    return None  # Si se agotan los intentos, retorna None

def process_invoice(pdf_file):
    """Procesa un solo archivo PDF en memoria y guarda los resultados en Google Sheets."""
    if pdf_file.filename.lower().endswith(".pdf"):  # Solo procesa archivos PDF
        print(f"Procesando {pdf_file.filename}...")
        try:
            pdf_text = extract_text_from_pdf(pdf_file)
            print(f"Texto extraído del PDF: {pdf_text[:100]}...")  # Log del texto extraído
            extracted_data = extract_invoice_data_using_gpt(pdf_text)
            
            if extracted_data:  # Asegurarse de que la extracción fue exitosa
                print(f"Datos extraídos: {extracted_data}")  # Log de los datos extraídos
                # Extraemos los datos y los agregamos a Google Sheets
                row = [
                    extracted_data["Razón Social del receptor"],
                    extracted_data["CUIT del receptor"],
                    extracted_data["Número de Comprobante"],
                    extracted_data["Fecha de Emisión"],
                    extracted_data["Importe Neto Gravado (USD)"],
                    extracted_data["Importe Neto Gravado ($)"],
                    extracted_data["IVA 21% (USD)"],
                    extracted_data["IVA 21% ($)"],
                    extracted_data["Importe Otros Tributos (USD)"],
                    extracted_data["Importe Otros Tributos ($)"],
                    extracted_data["Importe Total (USD)"],
                    extracted_data["Importe Total ($)"],
                    extracted_data["Valor Total en Pesos"]
                ]
                
                # Obtén las filas actuales en la hoja
                rows = worksheet.get_all_values()
                
                # Encuentra la siguiente fila vacía
                next_row = len(rows) + 1
                
                # Actualiza la fila en el rango correspondiente (A{next_row}:M{next_row})
                worksheet.update(f"A{next_row}:M{next_row}", [row])  # Actualiza la fila en las columnas de A a M
                print(f"Factura procesada y datos guardados en Google Sheets: {row}")
            
            time.sleep(10)  # Pausa entre procesamientos
        except Exception as e:
            print(f"Error al procesar archivo {pdf_file.filename}: {e}")
            raise  # Relanza la excepción para ver el traceback completo

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        if 'file' in request.files:  # Verifica si se envió el archivo
            files = request.files.getlist('file')  # Obtén la lista de archivos

            for file in files:
                if file.filename.lower().endswith(".pdf"):  # Solo procesa archivos PDF
                    try:
                        # Procesar el archivo directamente en memoria
                        process_invoice(file)
                    except Exception as e:
                        print(f"Error al procesar archivo {file.filename}: {e}")
                        return f"Error al procesar {file.filename}: {e}", 500  # Devuelve un error 500

            return redirect(url_for('index'))  # Redirige para mostrar los resultados
        else:
            return "No se encontraron archivos", 400  # Devuelve un error 400 si no hay archivos
    return render_template('index.html')



if __name__ == "__main__":
    # Comprobar si la variable de entorno PRODUCTION está definida (para Railway)
    if os.environ.get("PRODUCTION") == "true":
        # En producción (Railway), no se ejecuta app.run()
        pass  # Opcional: podrías agregar aquí código específico para producción
    else:
        # En desarrollo local, se ejecuta app.run()
        app.run(debug=True)  # debug=True para activar el modo de depuración