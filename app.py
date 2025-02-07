import os
import openai
import pdfplumber
import gspread
from google.oauth2.service_account import Credentials
import time
import json
from tenacity import retry, stop_after_attempt, wait_exponential
from flask import Flask, render_template, request, redirect, url_for, send_from_directory
import os
from dotenv import load_dotenv



app = Flask(__name__, static_folder='static')


# Carga las variables de entorno desde .env
load_dotenv()

# Obtén las variables de entorno
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GOOGLE_SHEETS_SCOPES = os.getenv("GOOGLE_SHEETS_SCOPES").split(",")  # Los scopes son una lista
GOOGLE_SHEETS_CREDENTIALS_FILE = os.getenv("GOOGLE_SHEETS_CREDENTIALS_FILE")
GOOGLE_SHEETS_URL = os.getenv("GOOGLE_SHEETS_URL")

# Configura tu clave API de OpenAI
openai.api_key = OPENAI_API_KEY

# Conexión con Google Sheets
credentials = Credentials.from_service_account_file(GOOGLE_SHEETS_CREDENTIALS_FILE, scopes=GOOGLE_SHEETS_SCOPES)
gc = gspread.authorize(credentials)

# Abre la hoja de cálculo por URL
spreadsheet = gc.open_by_url(GOOGLE_SHEETS_URL)
worksheet = spreadsheet.sheet1  # Usamos la primera hoja

def extract_text_from_pdf(pdf_path):
    """Extrae texto de un archivo PDF usando pdfplumber."""
    with pdfplumber.open(pdf_path) as pdf:
        text = "\n".join(page.extract_text() for page in pdf.pages if page.extract_text())
    return text

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))



def extract_invoice_data_using_gpt(pdf_text):
    """
    Usa la API de OpenAI para analizar el texto del PDF y extraer los datos de la factura.
    """
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
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4",  # Usa GPT-4 si está disponible
            messages=[
                {"role": "system", "content": "Eres un experto en análisis de texto y procesamiento de facturas."},
                {"role": "user", "content": prompt}
            ]
        )
        content = response['choices'][0]['message']['content']
    except openai.error.RateLimitError:
        print("Se alcanzó el límite de solicitudes. Esperando para reintentar...")
        time.sleep(60)  # Esperar 1 minuto antes de reintentar
        return None
    except Exception as e:
        print(f"Error en la API de OpenAI: {e}")
        return None

    # Intentar convertir la respuesta a JSON
    try:
        extracted_data = json.loads(content)
    except json.JSONDecodeError as e:
        print("Error al decodificar el JSON:", e)
        print("Respuesta del modelo:", content)
        return None

    return extracted_data

def process_invoice(pdf_path):
    """Procesa un solo archivo PDF y guarda los resultados en Google Sheets."""
    if pdf_path.lower().endswith(".pdf"):  # Solo procesa archivos PDF
        print(f"Procesando {pdf_path}...")
        pdf_text = extract_text_from_pdf(pdf_path)
        extracted_data = extract_invoice_data_using_gpt(pdf_text)
        
        if extracted_data:  # Asegurarse de que la extracción fue exitosa
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

def process_invoice(pdf_path):
    """Procesa un solo archivo PDF y guarda los resultados en Google Sheets."""
    if pdf_path.lower().endswith(".pdf"):  # Solo procesa archivos PDF
        print(f"Procesando {pdf_path}...")
        pdf_text = extract_text_from_pdf(pdf_path)
        extracted_data = extract_invoice_data_using_gpt(pdf_text)
        
        if extracted_data:  # Asegurarse de que la extracción fue exitosa
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
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        if 'file' in request.files:  # Verifica si se envió el archivo
            files = request.files.getlist('file')  # Obtén la lista de archivos
            pdf_dir = "./static/facturas"
            os.makedirs(pdf_dir, exist_ok=True)

            for file in files:
                if file.filename.lower().endswith(".pdf"):  # Solo procesa archivos PDF
                    file_path = os.path.join(pdf_dir, file.filename)
                    try:
                        file.save(file_path)
                        print(f"Archivo guardado: {file_path}")
                        # Procesar el archivo recién subido
                        process_invoice(file_path)
                    except Exception as e:
                        print(f"Error al guardar archivo {file.filename}: {e}")
                        return f"Error al guardar {file.filename}: {e}", 500  # Devuelve un error 500

            return redirect(url_for('index'))  # Redirige para mostrar los resultados
        else:
            return "No se encontraron archivos", 400  # Devuelve un error 400 si no hay archivos
    return render_template('index.html')

if __name__ == '__main__':
    app.run(debug=True)