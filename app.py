from flask import Flask, render_template, request, make_response, redirect, url_for
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime, timedelta
import pdfkit
import imgkit
import os
import shutil
import base64

app = Flask(__name__)

# Initialize Firebase
cred = credentials.Certificate("firebase_key.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

def get_logo_data_uri():
    logo_path = os.path.join(app.root_path, 'static', 'logo.png')
    if os.path.isfile(logo_path):
        with open(logo_path, 'rb') as logo_file:
            encoded = base64.b64encode(logo_file.read()).decode('utf-8')
            return f'data:image/png;base64,{encoded}'
    return ''

@app.route('/')
def index():
    return render_template('form.html')

@app.route('/generate', methods=['POST'])
def generate():
    today = datetime.now()
    issue_date_str = today.strftime("%d %b %Y")
    due_date_str = (today + timedelta(days=14)).strftime("%d %b %Y")

    # 1. Auto-increment Invoice Number in Firebase
    counter_ref = db.collection('metadata').document('counters')
    doc = counter_ref.get()
    
    if doc.exists:
        current_count = doc.to_dict().get('invoice_count', 0)
    else:
        current_count = 0
        
    new_invoice_number = current_count + 1
    counter_ref.set({'invoice_count': new_invoice_number})
    invoice_id_str = str(new_invoice_number)

    # 2. Build the Items List from multiple form entries
    item_names = request.form.getlist('item_name[]')
    item_descs = request.form.getlist('item_description[]')
    item_prices = request.form.getlist('item_price[]')
    item_qtys = request.form.getlist('item_quantity[]')
    # Assuming amounts are calculated or passed
    # You might need to adjust this loop if you are passing specific amounts
    items = []
    for i in range(len(item_names)):
        if item_names[i]:
            items.append({
                "name": item_names[i],
                "description": item_descs[i].replace('\n', '<br>'),
                "price": item_prices[i],
                "quantity": item_qtys[i]
            })

    # 3. Build the data dictionary and compute totals
    subtotal_value = float(request.form.get('subtotal', '0') or 0)
    discount_value = float(request.form.get('discount', '0') or 0)
    paid_amount_value = float(request.form.get('paid_amount', '0') or 0)
    invoice_total_value = max(subtotal_value - discount_value, 0)
    amount_due_value = max(invoice_total_value - paid_amount_value, 0)

    invoice_data = {
        "company_name": request.form.get('company_name'),
        "company_address": request.form.get('company_address').replace('\n', '<br>'),
        "company_phone": request.form.get('company_phone'),
        "company_email": request.form.get('company_email'),
        "client_name": request.form.get('client_name'),
        "client_phone": request.form.get('client_phone'),
        "invoice_number": invoice_id_str,
        "issue_date": issue_date_str,
        "due_date": due_date_str,
        "items": items, # This is now a list
        "subtotal": f"{subtotal_value:.2f}",
        "discount": f"{discount_value:.2f}",
        "paid_amount": f"{paid_amount_value:.2f}",
        "invoice_total": f"{invoice_total_value:.2f}",
        "amount_due": f"{amount_due_value:.2f}",
        "paid_in_full": amount_due_value == 0,
        "payments": [],
        "account_no": request.form.get('account_no'),
        "account_name": request.form.get('account_name'),
        "bank": request.form.get('bank'),
        "created_at": firestore.SERVER_TIMESTAMP 
    }

    # 4. Save to Firebase
    db.collection('invoices').document(invoice_id_str).set(invoice_data)
    return redirect(url_for('view_invoice', invoice_id=invoice_id_str))

@app.route('/history')
def history():
    invoices_ref = db.collection('invoices').order_by('created_at', direction=firestore.Query.DESCENDING).stream()
    invoices = []
    for doc in invoices_ref:
        data = doc.to_dict()
        data['id'] = doc.id
        invoices.append(data)
    return render_template('history.html', invoices=invoices)

@app.route('/invoice/<invoice_id>')
def view_invoice(invoice_id):
    doc = db.collection('invoices').document(invoice_id).get()
    if not doc.exists:
        return "Invoice not found", 404
    return render_template(
        'invoice.html',
        data=doc.to_dict(),
        show_buttons=True,
        invoice_id=invoice_id,
        logo_data_uri=get_logo_data_uri()
    )

@app.route('/invoice/<invoice_id>/payment', methods=['POST'])
def record_payment(invoice_id):
    doc_ref = db.collection('invoices').document(invoice_id)
    doc = doc_ref.get()
    if not doc.exists:
        return "Invoice not found", 404

    data = doc.to_dict()
    payment_amount = float(request.form.get('payment_amount', '0') or 0)
    if payment_amount <= 0:
        return redirect(url_for('view_invoice', invoice_id=invoice_id))

    invoice_total = float(data.get('invoice_total', '0') or 0)
    current_paid = float(data.get('paid_amount', '0') or 0)
    new_paid = current_paid + payment_amount
    new_amount_due = max(invoice_total - new_paid, 0)

    payments = data.get('payments', []) or []
    payments.append({
        'amount': f'{payment_amount:.2f}',
        'date': datetime.now().strftime("%d %b %Y"),
        'note': 'Installment payment'
    })

    doc_ref.update({
        'amount_due': f'{new_amount_due:.2f}',
        'paid_amount': f'{new_paid:.2f}',
        'paid_in_full': new_amount_due == 0,
        'payments': payments
    })

    return redirect(url_for('view_invoice', invoice_id=invoice_id))

def get_wkhtml_executable(env_var, names, fallback_paths):
    configured_path = os.environ.get(env_var)
    if configured_path and os.path.isfile(configured_path):
        return configured_path

    for name in names:
        executable_path = shutil.which(name)
        if executable_path:
            return executable_path

    for path in fallback_paths:
        if os.path.isfile(path):
            return path

    raise RuntimeError(
        f"{env_var} executable not found. Install wkhtmltopdf/wkhtmltoimage and set {env_var} to the binary path, or add it to PATH."
    )

def get_wkhtmltopdf_path():
    fallback_paths = []
    if os.name == 'nt':
        fallback_paths = [
            os.path.join(os.environ.get('ProgramFiles', 'C:\Program Files'), 'wkhtmltopdf', 'bin', 'wkhtmltopdf.exe'),
            os.path.join(os.environ.get('ProgramFiles(x86)', 'C:\Program Files (x86)'), 'wkhtmltopdf', 'bin', 'wkhtmltopdf.exe'),
        ]
    return get_wkhtml_executable('WKHTMLTOPDF_PATH', ['wkhtmltopdf', 'wkhtmltopdf.exe'], fallback_paths)

def get_wkhtmltoimage_path():
    fallback_paths = []
    if os.name == 'nt':
        fallback_paths = [
            os.path.join(os.environ.get('ProgramFiles', 'C:\Program Files'), 'wkhtmltopdf', 'bin', 'wkhtmltoimage.exe'),
            os.path.join(os.environ.get('ProgramFiles(x86)', 'C:\Program Files (x86)'), 'wkhtmltopdf', 'bin', 'wkhtmltoimage.exe'),
        ]
    return get_wkhtml_executable('WKHTMLTOIMAGE_PATH', ['wkhtmltoimage', 'wkhtmltoimage.exe'], fallback_paths)

@app.route('/download/pdf/<invoice_id>')
def download_pdf(invoice_id):
    doc = db.collection('invoices').document(invoice_id).get()
    data = doc.to_dict()
    rendered_html = render_template(
        'invoice.html',
        data=data,
        show_buttons=False,
        invoice_id=invoice_id,
        logo_data_uri=get_logo_data_uri()
    )
    options = {
        'enable-local-file-access': None,
        'page-size': 'A4',
        'margin-top': '0.75in',
        'margin-right': '0.55in',
        'margin-bottom': '0.5in',
        'margin-left': '0.5in'
    }
    wkhtmltopdf_path = get_wkhtmltopdf_path()
    config = pdfkit.configuration(wkhtmltopdf=wkhtmltopdf_path)
    pdf = pdfkit.from_string(rendered_html, False, options=options, configuration=config)
    
    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename=Invoice_{data["invoice_number"]}.pdf'
    return response

@app.route('/download/jpeg/<invoice_id>')
def download_jpeg(invoice_id):
    doc = db.collection('invoices').document(invoice_id).get()
    data = doc.to_dict()
    rendered_html = render_template(
        'invoice.html',
        data=data,
        show_buttons=False,
        invoice_id=invoice_id,
        logo_data_uri=get_logo_data_uri()
    )
    options = {'format': 'jpeg', 'quality': 100, 'zoom': 2, 'width': 1200, 'enable-local-file-access': None}
    img_path = get_wkhtmltoimage_path()
    config = imgkit.config(wkhtmltoimage=img_path)
    img = imgkit.from_string(rendered_html, False, options=options, config=config)
    
    response = make_response(img)
    response.headers['Content-Type'] = 'image/jpeg'
    response.headers['Content-Disposition'] = f'attachment; filename=Invoice_{data["invoice_number"]}.jpg'
    return response

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True)