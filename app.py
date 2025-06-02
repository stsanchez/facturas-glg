from flask import Flask, request, render_template, redirect, url_for, flash
import os
import openai
import pdfplumber
import json
import logging
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
import gspread
import time
import re
import io
import base64

# === Configuración inicial ===
load_dotenv()
app = Flask(__name__)
app.secret_key = 'supersecretkey'  # Necesario para mensajes flash

UPLOAD_FOLDER = 'pdf'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# === Configuración de OpenAI y Google Sheets ===
logging.basicConfig(level=logging.INFO)
openai.api_key = os.getenv("OPENAI_API_KEY")
if not openai.api_key:
    logging.error("❌ No se encontró la API KEY de OpenAI.")
    exit(1)


# Configuración de Google Sheets
GOOGLE_SHEETS_SCOPES = os.getenv("GOOGLE_SHEETS_SCOPES").split(",")
GOOGLE_SHEETS_CREDENTIALS = os.getenv("GOOGLE_SHEETS_CREDENTIALS")
GOOGLE_SHEETS_URL = os.getenv("GOOGLE_SHEETS_URL")

credentials_info = json.loads(GOOGLE_SHEETS_CREDENTIALS)
credentials = Credentials.from_service_account_info(credentials_info, scopes=GOOGLE_SHEETS_SCOPES)
gc = gspread.authorize(credentials)
spreadsheet = gc.open_by_url(GOOGLE_SHEETS_URL)
worksheet = spreadsheet.worksheet("Clientes")
# === Funciones auxiliares ===

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

def upload_pdf_to_drive(filepath, filename):
    credentials_drive = Credentials.from_service_account_info(credentials_info, scopes=GOOGLE_SHEETS_SCOPES)
    drive_service = build('drive', 'v3', credentials=credentials_drive)

    file_metadata = {
        'name': filename,
        'mimeType': 'application/pdf'
    }

    media = MediaFileUpload(filepath, mimetype='application/pdf')
    uploaded_file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()

    # Hacer el archivo público para que cualquiera con el enlace pueda verlo
    drive_service.permissions().create(
        fileId=uploaded_file['id'],
        body={'role': 'reader', 'type': 'anyone'},
    ).execute()

    file_link = f"https://drive.google.com/file/d/{uploaded_file['id']}/view?usp=sharing"
    return file_link

def limpiar_importe(valor: str) -> float:
    if not valor:
        return 0.0
    valor = valor.replace('$', '').replace(' ', '')
    if ',' in valor and '.' in valor and valor.find(',') > valor.find('.'):
        valor = valor.replace('.', '').replace(',', '.')
    elif ',' in valor and '.' in valor and valor.find('.') > valor.find(','):
        valor = valor.replace(',', '')
    elif ',' in valor and not '.' in valor:
        valor = valor.replace('.', '').replace(',', '.')
    else:
        valor = valor.replace(',', '')
    try:
        return float(valor)
    except ValueError:
        return 0.0


def load_prompt_template(filename):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logging.error(f"❌ No se encontró el archivo de prompt: {filename}")
        return ""

def extract_text_from_pdf_file(filename):
    try:
        with pdfplumber.open(filename) as pdf:
            if pdf.pages:
                return pdf.pages[0].extract_text() or ""
    except Exception as e:
        logging.error(f"❌ Error al abrir PDF: {e}")
    return ""

def clean_json_string(response_text):
    try:
        match = re.search(r"\{.*\}", response_text, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception as e:
        logging.error(f"❌ Error limpiando JSON: {e}")
    return None

def extract_invoice_data_using_gpt(pdf_text):
    prompt_template = load_prompt_template("prompt.txt")
    if not prompt_template:
        return None
    prompt = prompt_template.replace("{pdf_text}", pdf_text)
    for attempt in range(3):
        try:
            response = openai.chat.completions.create(
                model="gpt-4-turbo",
                messages=[
                    {"role": "system", "content": "Eres un experto en análisis de facturas de proveedores. Devuelve SOLO un JSON válido."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=600,
                temperature=0
            )
            content = response.choices[0].message.content.strip()
            data = clean_json_string(content)
            if data:
                return data
            time.sleep(2 * (attempt + 1))
        except Exception as e:
            logging.warning(f"⚠️ Error intento {attempt + 1}: {e}")
    return None

def insert_data_into_sheet(data):
    if len(worksheet.get_all_values()) == 0:
        worksheet.append_row(list(data.keys()))

    campos_a_convertir = ["Importe Total", "Importe total en pesos", "Importe Neto Gravado", "IVA 21%", "Otros Tributos"]
    cleaned_data = {k: limpiar_importe(v) if k in campos_a_convertir else v for k, v in data.items()}
    worksheet.append_row(list(cleaned_data.values()))

from googleapiclient.http import MediaIoBaseUpload

def upload_pdf_to_drive_bytes(pdf_bytes, filename):
    credentials_drive = Credentials.from_service_account_info(credentials_info, scopes=GOOGLE_SHEETS_SCOPES)
    drive_service = build('drive', 'v3', credentials=credentials_drive)

    file_metadata = {
        'name': filename,
        'mimeType': 'application/pdf'
    }

    media = MediaIoBaseUpload(io.BytesIO(pdf_bytes), mimetype='application/pdf')
    uploaded_file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()

    drive_service.permissions().create(
        fileId=uploaded_file['id'],
        body={'role': 'reader', 'type': 'anyone'},
    ).execute()

    return f"https://drive.google.com/file/d/{uploaded_file['id']}/view?usp=sharing"

# === Rutas de Flask ===
@app.route('/')
def index():
    return render_template("upload.html")

@app.route('/procesar', methods=['POST'])
def procesar_pdf():
    if 'archivo' not in request.files:
        flash("No se envió ningún archivo.")
        return redirect(url_for('index'))

    archivo = request.files['archivo']
    if archivo.filename == '':
        flash("No se seleccionó ningún archivo.")
        return redirect(url_for('index'))

    if archivo and archivo.filename.lower().endswith('.pdf'):

        #filepath = os.path.join(app.config['UPLOAD_FOLDER'], archivo.filename)
        #archivo.save(filepath)
        #pdf_text = extract_text_from_pdf_file(filepath)
        pdf_bytes = archivo.read()
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            if pdf.pages:
                pdf_text = pdf.pages[0].extract_text() or ""
            else:
                pdf_text = ""


        if not pdf_text:
            flash("No se pudo extraer texto del PDF.")
            return redirect(url_for('index'))

        extracted_data = extract_invoice_data_using_gpt(pdf_text)
        if not extracted_data:
            flash("No se pudieron extraer los datos correctamente.")
            return redirect(url_for('index'))

        # 👉 Mostrar formulario editable con los datos
        #return render_template("result_form.html", datos=extracted_data, pdf_filename=archivo.filename)
        pdf_base64 = base64.b64encode(pdf_bytes).decode('utf-8')
        return render_template("result_form.html", datos=extracted_data, pdf_data=pdf_base64, pdf_filename=archivo.filename)


    else:
        flash("El archivo debe ser un PDF.")
        return redirect(url_for('index'))
    
from datetime import datetime

@app.context_processor
def inject_current_year():
    return {'current_year': datetime.now().year}

@app.route('/confirmar', methods=['POST'])
def confirmar_datos():
    datos_confirmados = request.form.to_dict()

    if not datos_confirmados:
        flash("No se recibieron datos para confirmar.")
        return redirect(url_for('index'))

    # Extraer nombre del archivo y base64 del PDF
    filename = datos_confirmados.pop("pdf_filename", None)
    pdf_base64 = datos_confirmados.pop("pdf_data", None)

    if not filename or not pdf_base64:
        flash("Faltan datos del archivo PDF.")
        return redirect(url_for('index'))

    try:
        # Decodificar PDF y subirlo a Google Drive
        pdf_bytes = base64.b64decode(pdf_base64)
        link_pdf = upload_pdf_to_drive_bytes(pdf_bytes, filename)
        datos_confirmados["Link PDF"] = link_pdf
    except Exception as e:
        logging.error(f"❌ Error al subir PDF a Drive: {e}")
        datos_confirmados["Link PDF"] = "No disponible"

    # Insertar los datos en Google Sheets
    insert_data_into_sheet(datos_confirmados)

    flash("✅ Datos confirmados, PDF subido a Drive e insertado en la hoja.")
    return redirect(url_for('index'))




# === Run local ===
if __name__ == "__main__":
    if os.getenv("PRODUCTION") == "true":
        # En Railway u otro entorno productivo
        pass
    else:
        app.run(debug=True)
#a