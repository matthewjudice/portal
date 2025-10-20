import json
import random
import uuid
from flask import Flask, request, jsonify, url_for, Response
from flask_cors import CORS
# NOTE: The 'requests' library is used to simulate calling the external ePay API.
import requests 
from requests.auth import HTTPBasicAuth
import urllib3

# Suppress the InsecureRequestWarning from using verify=False
requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)


app = Flask(__name__)
# Enable CORS for communication with the frontend
CORS(app)

# --- CONFIGURATION (UPDATED WITH REGENERATED CREDENTIALS) ---

EPAY_API_KEY = "376f59d731aa4ea"  
EPAY_API_SECRET = "4f4eaf61f7754dd" 

EPAY_BASE_URL = "https://api.epaypolicydemo.com:443/api/v1"

# --- IN-MEMORY MOCK DATABASE ---
customers = {}
invoices = {}
tokens = {}
transactions = {}

# Pre-populate with mock data for testing flow
customers['cust-1'] = {'id': 'cust-1', 'name': 'Alice Johnson', 'email': 'alice@example.com', 'phone': '555-1234', 'tokenId': None}
invoices['inv-1'] = {'id': 'inv-1', 'customerId': 'cust-1', 'invoiceNumber': 'INV-1001', 'amount': 75.50, 'status': 'Outstanding', 'transactionId': None}

# --- UTILITY FUNCTIONS ---

def api_error(message, status_code):
    """Returns a standardized JSON error response."""
    response = jsonify({'error': message})
    response.status_code = status_code
    return response

# --- ROOT WELCOME ROUTE ---

@app.route('/')
def index():
    """Simple status check for the root URL."""
    return jsonify({
        "status": "Flask API is running",
        "message": "Access API endpoints via /api/..."
    })

# =========================================================
# === CUSTOMER AND INVOICE ENDPOINTS (LOCAL MOCK) =========
# =========================================================

@app.route('/api/customers', methods=['GET'])
def list_customers():
    return jsonify(list(customers.values()))

@app.route('/api/customers', methods=['POST'])
def add_customer():
    data = request.get_json()
    if not all(k in data for k in ('name', 'email')):
        return api_error("Missing required fields: name or email", 400)
        
    customer_id = f"cust-{str(uuid.uuid4())[:8]}"
    # Ensure a tokenId field is initialized
    new_customer = {'id': customer_id, 'tokenId': None, **data}
    customers[customer_id] = new_customer
    
    response = jsonify(new_customer)
    response.status_code = 201
    response.headers['Location'] = url_for('get_customer', customer_id=customer_id, _external=True)
    return response

@app.route('/api/customers/<customer_id>', methods=['GET'])
def get_customer(customer_id):
    if customer_id not in customers:
        return api_error("Customer not found", 404)
    return jsonify(customers[customer_id])

# --- NEW ENDPOINT: LINK TOKEN TO CUSTOMER ---
@app.route('/api/customers/<customer_id>/token', methods=['POST'])
def save_customer_token(customer_id):
    """Links an existing tokenId (obtained via POST /api/epay/tokens) to a local customer."""
    if customer_id not in customers:
        return api_error("Customer not found", 404)
        
    data = request.get_json()
    token_id = data.get('tokenId')
    
    if not token_id:
        return api_error("Missing 'tokenId' in request body.", 400)
    
    # Store the tokenId on the customer record
    customers[customer_id]['tokenId'] = token_id
    
    return jsonify({
        'message': 'Token successfully linked to customer.',
        'customer': customers[customer_id]
    })
# --- END NEW ENDPOINT ---


@app.route('/api/invoices', methods=['GET'])
def list_invoices():
    customer_id = request.args.get('customerId')
    if customer_id and customer_id in customers:
        filtered_invoices = [inv for inv in invoices.values() if inv['customerId'] == customer_id]
        return jsonify(filtered_invoices)
    
    return jsonify(list(invoices.values()))

@app.route('/api/invoices', methods=['POST'])
def add_invoice():
    data = request.get_json()
    if not all(k in data for k in ('customerId', 'amount')):
        return api_error("Missing required fields: customerId or amount", 400)

    invoice_id = f"inv-{str(uuid.uuid4())[:8]}"
    invoice_number = f"INV-{random.randint(2000, 9999)}"
    
    new_invoice = {
        'id': invoice_id,
        'customerId': data['customerId'],
        'invoiceNumber': invoice_number,
        'amount': float(data['amount']),
        'status': 'Outstanding',
        'transactionId': None
    }
    invoices[invoice_id] = new_invoice
    
    response = jsonify(new_invoice)
    response.status_code = 201
    return response

@app.route('/api/invoices/<invoice_id>/paid', methods=['POST'])
def mark_invoice_paid(invoice_id):
    if invoice_id not in invoices:
        return api_error("Invoice not found", 404)
        
    data = request.get_json()
    transaction_id = data.get('transactionId')

    invoice = invoices[invoice_id]
    invoice['status'] = 'Paid'
    invoice['transactionId'] = transaction_id
    
    return jsonify(invoice)

# =========================================================
# === EPAY API ENDPOINTS (REAL CALL IMPLEMENTATION) =======
# =========================================================

# --- 1. POST /epay/tokens (REAL TOKEN CREATION - FLATTENED PAYLOAD) ---
@app.route('/api/epay/tokens', methods=['POST'])
def create_token():
    data = request.get_json()
    
    # 1. Input Validation & Data Sanitization
    is_cc = 'creditCardInformation' in data
    is_ach = 'bankAccountInformation' in data
    
    # Check for required customer-related fields expected by the external API
    if not all(k in data for k in ('emailAddress', 'payerName')):
        return api_error("Missing required customer fields: 'emailAddress' or 'payerName'.", 400)

    external_data = data.copy()

    # Clean up the payload based on payment type
    if is_cc and is_ach:
        del external_data['bankAccountInformation'] 
    elif is_ach:
        if 'creditCardInformation' in external_data:
            del external_data['creditCardInformation']
    elif is_cc:
        if 'bankAccountInformation' in external_data:
            del external_data['bankAccountInformation']
    else:
        return api_error("Missing 'creditCardInformation' or 'bankAccountInformation' in request body.", 400)

    # 2. Prepare Headers and Auth for External Call
    auth = HTTPBasicAuth(EPAY_API_KEY, EPAY_API_SECRET)
    headers = {
        'Content-Type': 'application/json',
        'Connection': 'close' 
    }

    # 3. EXECUTE REAL EXTERNAL API CALL
    try:
        real_response = requests.post(
            f"{EPAY_BASE_URL}/tokens", 
            headers=headers, 
            json=external_data, # Use the flattened data here
            auth=auth,
            timeout=30,
            verify=False 
        )
        real_response.raise_for_status()
        
        response_data = real_response.json()
        token_id = response_data.get('tokenId') or response_data.get('id')
        
        if not token_id:
            return api_error("Token API succeeded but did not return a Token ID.", 500)

        tokens[token_id] = data
        
        # 4. Prepare Flask Response (201 Created)
        flask_response = jsonify({'tokenId': token_id, 'message': 'Token created successfully.'})
        flask_response.status_code = 201
        
        if 'Location' in real_response.headers:
             flask_response.headers['Location'] = real_response.headers['Location']
        
        return flask_response

    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code
        try:
            error_message = e.response.json().get('error') or e.response.json().get('message') or e.response.text
        except:
            error_message = e.response.text
            
        return api_error(f"External Token API Error ({status_code}): {error_message}", status_code)
    except requests.exceptions.RequestException as e:
        diagnostic_message = (
            f"Network Error (503): Connection failed. Check if API Key/Secret is correct, "
            f"or if your firewall blocks port 443 access to {EPAY_BASE_URL}. (Check IP Whitelisting)"
        )
        return api_error(diagnostic_message, 503)
    except Exception as e:
        return api_error(f"Server-side error during token creation: {str(e)}", 500)


# --- 2. GET /epay/fees (STILL MOCK) ---
@app.route('/api/epay/fees', methods=['GET'])
def get_fees():
    amount = request.args.get('amount')
    try:
        amount = float(amount)
        if amount <= 0:
            return api_error("Amount must be a positive number.", 400)
    except (TypeError, ValueError):
        return api_error("Invalid amount parameter.", 400)

    # --- MOCK FEE CALCULATION FOR DEMO ---
    cc_fee = round(amount * 0.03 + 0.30, 2)
    ach_fee = round(amount * 0.005 + 0.05, 2)
    
    return jsonify({
        'creditCardPayerFee': f"{cc_fee:.2f}",
        'achPayerFee': f"{ach_fee:.2f}",
        'message': "Mock fees calculated successfully."
    })


# --- 3. POST /epay/transactions (REAL PAYMENT PROCESSING) ---
@app.route('/api/epay/transactions', methods=['POST'])
def post_transaction():
    data = request.get_json()
    
    if not all(k in data for k in ('amount', 'tokenId')):
        return api_error("Missing required fields: amount or tokenId.", 400)
        
    invoice_id = data.pop('invoiceId', None) # Remove local field before sending to ePay

    # 1. Prepare Auth for External Call
    auth = HTTPBasicAuth(EPAY_API_KEY, EPAY_API_SECRET)
    headers = {
        'Content-Type': 'application/json',
        'Connection': 'close'
    }

    # 2. EXECUTE REAL EXTERNAL API CALL
    try:
        real_response = requests.post(
            f"{EPAY_BASE_URL}/transactions", 
            headers=headers, 
            json=data, # Use data without invoiceId
            auth=auth,
            timeout=30,
            verify=False
        )
        real_response.raise_for_status()
        
        response_data = real_response.json()
        txn_id = response_data.get('transactionId') or response_data.get('id')
        public_id = response_data.get('publicId')

        if not txn_id:
            return api_error("Transaction API succeeded but did not return a Transaction ID.", 500)

        new_transaction = {
            'id': txn_id,
            'publicId': public_id,
            'status': response_data.get('status', 'Completed'),
            'details': data,
            'local_invoice_id': invoice_id
        }
        transactions[txn_id] = new_transaction
        
        # 3. LOCAL UPDATE: Mark invoice as paid if transaction succeeded and invoiceId was provided
        if invoice_id and invoice_id in invoices:
             invoices[invoice_id]['status'] = 'Paid'
             invoices[invoice_id]['transactionId'] = txn_id
        
        # 4. Prepare Flask Response (201 Created)
        flask_response = jsonify({'id': txn_id, 'publicId': public_id, 'invoiceStatusUpdated': (invoice_id is not None)})
        flask_response.status_code = 201
        
        if 'Location' in real_response.headers:
             flask_response.headers['Location'] = real_response.headers['Location']
        
        return flask_response

    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code
        try:
            error_message = e.response.json().get('error') or e.response.json().get('message') or e.response.text
        except:
            error_message = e.response.text
            
        return api_error(f"External Transaction API Error ({status_code}): {error_message}", status_code)
    except requests.exceptions.RequestException as e:
        diagnostic_message = (
            f"Network Error (503): Connection failed. Check if API Key/Secret is correct, "
            f"or if your firewall blocks port 443 access to {EPAY_BASE_URL}. (Check IP Whitelisting)"
        )
        return api_error(diagnostic_message, 503)
    except Exception as e:
        return api_error(f"Server-side error during transaction post: {str(e)}", 500)


# --- 4. GET /epay/transactions/<txn_id> (REAL STATUS CHECK) ---
@app.route('/api/epay/transactions/<transaction_id>', methods=['GET'])
def get_transaction(transaction_id):
    
    # 1. Check local cache first
    if transaction_id in transactions:
        return jsonify(transactions[transaction_id])
    
    # 2. Prepare Auth for External Call
    auth = HTTPBasicAuth(EPAY_API_KEY, EPAY_API_SECRET)
    headers = {
        'Content-Type': 'application/json',
        'Connection': 'close'
    }

    # 3. EXECUTE REAL EXTERNAL API CALL
    try:
        real_response = requests.get(
            f"{EPAY_BASE_URL}/transactions/{transaction_id}", 
            headers=headers, 
            auth=auth,
            timeout=30,
            verify=False
        )
        real_response.raise_for_status()
        
        transaction_details = real_response.json()
        
        # Update local cache with fetched data
        transactions[transaction_id] = transaction_details

        return jsonify(transaction_details)

    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code
        try:
            error_message = e.response.json().get('error') or e.response.json().get('message') or e.response.text
        except:
            error_message = e.response.text
            
        return api_error(f"External Status Check API Error ({status_code}): {error_message}", status_code)
    except requests.exceptions.RequestException as e:
        diagnostic_message = (
            f"Network Error (503): Connection failed. Check if API Key/Secret is correct, "
            f"or if your firewall blocks port 443 access to {EPAY_BASE_URL}. (Check IP Whitelisting)"
        )
        return api_error(diagnostic_message, 503)
    except Exception as e:
        return api_error(f"Server-side error during status check: {str(e)}", 500)


# --- RUNNER ---
if __name__ == '__main__':
    # NOTE: In production (like on Render), this block is ignored; Gunicorn runs the app.
    app.run(debug=True)