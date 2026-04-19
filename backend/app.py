from flask import Flask, jsonify
from flask_cors import CORS
from SEC_Financials_Final import get_financial_data

app = Flask(__name__)
# Enable CORS so the HTML/JS frontend can communicate with this API
CORS(app)

@app.route('/api/financials/<ticker>', methods=['GET'])
def get_financials(ticker):
    try:
        # Call your SEC scraping function, defaulting to 5 years
        data = get_financial_data(ticker, 5)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)