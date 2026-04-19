from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_caching import Cache
from SEC_Financials_Final import get_financial_data

app = Flask(__name__)
# Enable CORS so the HTML/JS frontend can communicate with this API
CORS(app)

# Configure Cache
cache = Cache(app, config={'CACHE_TYPE': 'SimpleCache', 'CACHE_DEFAULT_TIMEOUT': 86400})

@app.route('/api/financials/<ticker>', methods=['GET'])
@cache.cached(timeout=86400, query_string=True)
def get_financials(ticker):
    try:
        # Get query parameters with default fallbacks and type conversion
        years = request.args.get('years', default=5, type=int)
        freq = request.args.get('freq', default='annual', type=str)
        
        # Call your SEC scraping function
        data = get_financial_data(ticker, years, freq)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)