import streamlit as st
import requests
import pandas as pd
import plotly.express as px

# Set up the page layout
st.set_page_config(page_title="SEC Financials Visualizer", layout="wide")
st.title("SEC Financial Data Dashboard")

# ------------------------------------------------------------------
# Sidebar Inputs
# ------------------------------------------------------------------
st.sidebar.header("Configuration")
ticker = st.sidebar.text_input("Ticker Symbol", value="AAPL").upper()
years = st.sidebar.number_input("Years of History", min_value=1, max_value=20, value=5)
freq = st.sidebar.selectbox("Frequency", options=["Annual", "Quarterly"]).lower()

# ------------------------------------------------------------------
# Helper Function: Clean formatted strings into floats for charting
# ------------------------------------------------------------------
def clean_currency(val):
    """
    Converts formatted strings like '$1.23B' or '-$450.00M' back into raw floats.
    """
    if not isinstance(val, str):
        return val
    if val == "—":
        return None
        
    val = val.replace(',', '').strip()
    
    multiplier = 1
    if val.endswith('B'):
        multiplier = 1e9
        val = val[:-1]
    elif val.endswith('M'):
        multiplier = 1e6
        val = val[:-1]
    elif val.endswith('K'):
        multiplier = 1e3
        val = val[:-1]
    elif val.endswith('%'):
        val = val[:-1]
        
    val = val.replace('$', '')
    try:
        return float(val) * multiplier
    except ValueError:
        return None

# ------------------------------------------------------------------
# Main Execution
# ------------------------------------------------------------------
if st.sidebar.button("Load Data"):
    if not ticker:
        st.error("Please enter a ticker symbol.")
    else:
        with st.spinner(f"Fetching {freq} data for {ticker}..."):
            url = f"http://127.0.0.1:5000/api/financials/{ticker}?years={years}&freq={freq}"
            try:
                response = requests.get(url)
                response.raise_for_status()
                data = response.json()
                
                if "error" in data:
                    st.error(data["error"])
                else:
                    # Convert JSON to DataFrame
                    df = pd.DataFrame.from_dict(data, orient="index")
                    
                    # Transpose and clean data for charting
                    df_plot = df.T
                    df_plot = df_plot.applymap(clean_currency)
                    
                    # Scale large dollar amounts to Billions for clean axes
                    billions_metrics = [
                        "Revenue", "Cost of Goods Sold", "Gross Profit", "SG&A Expense", 
                        "R&D Expense", "Operating Income", "Net Income", "Operating Cash Flow", 
                        "Capital Expenditures", "Free Cash Flow"
                    ]
                    for col in billions_metrics:
                        if col in df_plot.columns:
                            df_plot[col] = df_plot[col] / 1e9
                            
                    if "Capital Expenditures" in df_plot.columns:
                        # Display CapEx as a negative outflow for visual distinction
                        df_plot["Capital Expenditures"] = -df_plot["Capital Expenditures"].abs()
                        
                    # Sort chronologically for charts
                    df_plot = df_plot.iloc[::-1]
                    df_plot.index.name = "Period"
                    df_plot = df_plot.reset_index()
                    
                    # Create dashboard tabs
                    tab1, tab2, tab3, tab4 = st.tabs(["Income Statement", "Cash Flow", "Shareholder Value", "Raw Data"])
                    
                    with tab1:
                        st.subheader("Income Statement ($ Billions)")
                        is_cols = [c for c in ["Revenue", "Gross Profit", "Operating Income", "Net Income"] if c in df_plot.columns]
                        if is_cols:
                            fig_is = px.bar(df_plot, x="Period", y=is_cols, barmode='group', template='plotly_dark', title="Income Statement Metrics")
                            fig_is.update_layout(yaxis_title="$ Billions", legend_title="Metric")
                            st.plotly_chart(fig_is, use_container_width=True)
                            
                    with tab2:
                        st.subheader("Cash Flow ($ Billions)")
                        cf_cols = [c for c in ["Operating Cash Flow", "Capital Expenditures", "Free Cash Flow"] if c in df_plot.columns]
                        if cf_cols:
                            fig_cf = px.bar(df_plot, x="Period", y=cf_cols, barmode='group', template='plotly_dark', title="Cash Flow Generation")
                            fig_cf.update_layout(yaxis_title="$ Billions", legend_title="Metric")
                            st.plotly_chart(fig_cf, use_container_width=True)
                            
                    with tab3:
                        st.subheader("Shareholder Value")
                        if "Basic Shares Outstanding" in df_plot.columns:
                            fig_shares = px.line(df_plot, x="Period", y="Basic Shares Outstanding", template='plotly_dark', title="Shares Outstanding", markers=True)
                            st.plotly_chart(fig_shares, use_container_width=True)
                            
                        val_cols = [c for c in ["EPS Diluted", "Dividends Per Share"] if c in df_plot.columns]
                        if val_cols:
                            fig_val = px.line(df_plot, x="Period", y=val_cols, template='plotly_dark', title="Per Share Metrics", markers=True)
                            fig_val.update_layout(yaxis_title="$ / Share", legend_title="Metric")
                            st.plotly_chart(fig_val, use_container_width=True)
                            
                    with tab4:
                        st.subheader("Raw Financial Data")
                        st.dataframe(df)
                    
            except requests.exceptions.RequestException as e:
                st.error(f"Failed to connect to the Flask API. Is it running at http://127.0.0.1:5000?\n\nError: {e}")