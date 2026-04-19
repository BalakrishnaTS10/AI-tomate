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
                    st.session_state['api_data'] = data
                    
            except requests.exceptions.RequestException as e:
                st.error(f"Failed to connect to the Flask API. Is it running at http://127.0.0.1:5000?\n\nError: {e}")

# Create dashboard tabs outside the button block so forms don't reset the page
tab1, tab2, tab3, tab4, tab5 = st.tabs(["Income Statement", "Cash Flow", "Shareholder Value", "Raw Data", "DCF Calculator"])

if 'api_data' in st.session_state:
    data = st.session_state['api_data']
    
    # Convert JSON to DataFrame
    df = pd.DataFrame.from_dict(data, orient="index")
    
    # Transpose and clean data for charting
    df_plot = df.T
    raw_clean_df = df_plot.applymap(clean_currency) # Keep a copy of unscaled raw numbers for the DCF
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
    df_plot.index.name = "Period"
    df_plot = df_plot.reset_index()
    df_plot = df_plot.sort_values(by="Period", ascending=True)
    
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

    with tab5:
        st.subheader("DCF Calculator")
        
        # Ensure the DataFrame is sorted descending by period to grab the newest data first
        raw_clean_df = raw_clean_df.sort_index(ascending=False)
        
        # Extract raw metrics from the latest year
        latest_period = raw_clean_df.index[0]
        raw_fcf = raw_clean_df.loc[latest_period].get("Free Cash Flow", 0.0)
        raw_shares = raw_clean_df.loc[latest_period].get("Basic Shares Outstanding", 0.0)
        
        # Handle potential missing data
        if pd.isna(raw_fcf): raw_fcf = 0.0
        if pd.isna(raw_shares): raw_shares = 0.0
        
        st.write(f"**Debug - Raw FCF ({latest_period}):**", raw_fcf)
        st.write(f"**Debug - Raw Shares ({latest_period}):**", raw_shares)
        
        # Pre-fill conversion logic
        fcf_b_default = float(raw_fcf) / 1e9 if raw_fcf else 0.0
        shares_m_default = float(raw_shares) / 1e6 if raw_shares else 0.0
        
        # Calculate 3-Year FCF CAGR
        cagr_default = 0.0
        if len(raw_clean_df) >= 4:
            past_period = raw_clean_df.index[3] # 3 years prior
            past_fcf = raw_clean_df.loc[past_period].get("Free Cash Flow", 0.0)
            if pd.isna(past_fcf): past_fcf = 0.0
            
            if past_fcf > 0 and raw_fcf > 0:
                cagr = ((raw_fcf / past_fcf) ** (1/3)) - 1
                cagr_default = round(cagr * 100, 2)
                
        with st.form('dcf_form'):
            col1, col2 = st.columns(2)
            with col1:
                fcf_input = st.number_input("Current FCF ($B)", value=fcf_b_default, format="%.2f")
                growth_rate = st.number_input("Growth Rate Y1-Y5 (%)", value=float(cagr_default), format="%.2f")
                terminal_rate = st.number_input("Terminal Growth Rate (%)", value=2.5, format="%.2f")
            with col2:
                shares_input = st.number_input("Shares Outstanding (Millions)", value=shares_m_default, format="%.2f")
                discount_rate = st.number_input("Discount Rate (%)", value=10.0, format="%.2f")
                mos = st.number_input("Margin of Safety (%)", value=20.0, format="%.2f")
                
            submit_dcf = st.form_submit_button("Calculate Intrinsic Value")
            
            if submit_dcf:
                if discount_rate <= terminal_rate:
                    st.error("Discount Rate must be strictly greater than the Terminal Growth Rate.")
                elif shares_input <= 0:
                    st.error("Shares Outstanding must be greater than 0.")
                else:
                    # DCF Math Logic
                    g = growth_rate / 100
                    tr = terminal_rate / 100
                    dr = discount_rate / 100
                    
                    total_pv_fcf = 0
                    proj_fcf = fcf_input
                    for year in range(1, 6):
                        proj_fcf *= (1 + g)
                        pv = proj_fcf / ((1 + dr) ** year)
                        total_pv_fcf += pv
                        
                    year6_fcf = proj_fcf * (1 + tr)
                    tv = year6_fcf / (dr - tr)
                    pv_tv = tv / ((1 + dr) ** 5)
                    
                    ev_b = total_pv_fcf + pv_tv
                    intrinsic_value = (ev_b * 1000) / shares_input
                    target_buy = intrinsic_value * (1 - mos / 100)
                    
                    # Display results beautifully
                    st.markdown("---")
                    res_col1, res_col2, res_col3 = st.columns(3)
                    res_col1.metric("Intrinsic Value / Share", f"${intrinsic_value:,.2f}")
                    res_col2.metric("Target Buy Price", f"${target_buy:,.2f}")
                    res_col3.metric("Enterprise Value", f"${ev_b:,.2f}B")

else:
    with tab5:
        st.warning("Please enter a ticker and click 'Load Data' from the sidebar first.")