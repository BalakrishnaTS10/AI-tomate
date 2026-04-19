document.getElementById('fetch-data-btn').addEventListener('click', async function() {
    const ticker = document.getElementById('ticker').value.trim();
    const years = document.getElementById('years').value;
    const freq = document.getElementById('freq').value;

    if (!ticker) {
        alert("Please enter a ticker symbol.");
        return;
    }

    const fetchBtn = document.getElementById('fetch-data-btn');
    fetchBtn.textContent = "Fetching...";
    const loadingIndicator = document.getElementById('loading-indicator');
    loadingIndicator.style.display = 'block';

    try {
        // Fetch data from the Flask API
        const response = await fetch(`http://127.0.0.1:5000/api/financials/${ticker}?years=${years}&freq=${freq}`);
        const data = await response.json();

        // Log data to inspect the structure in Developer Tools
        console.log("API Response Data:", data);

        if (data.error) {
            alert(`Error fetching data: ${data.error}`);
            return;
        }

        // Placeholder logic: Extract Free Cash Flow
        // The API returns data like: { "Free Cash Flow": { "FY2023": "$100.50B", "FY2022": ... } }
        const fcfData = data["Free Cash Flow"];
        if (fcfData) {
            // Sort keys to find the latest year
            const latestYear = Object.keys(fcfData).sort().reverse()[0];
            let latestFcf = fcfData[latestYear];
            
            // Remove characters like '$', 'B', 'M', or commas if the API returns formatted strings
            if (typeof latestFcf === 'string') {
                latestFcf = parseFloat(latestFcf.replace(/[^0-9.-]+/g, ""));
            }
            document.getElementById('fcf').value = latestFcf;
        }

        // Placeholder logic: Extract Shares Outstanding
        const sharesData = data["Basic Shares Outstanding"] || data["Diluted Shares Outstanding"];
        if (sharesData) {
            const latestYear = Object.keys(sharesData).sort().reverse()[0];
            let latestShares = sharesData[latestYear];
            
            if (typeof latestShares === 'string') {
                latestShares = parseFloat(latestShares.replace(/[^0-9.-]+/g, ""));
            }
            document.getElementById('shares').value = latestShares;
        }

    } catch (error) {
        console.error("Error fetching data:", error);
        alert("Failed to fetch data. Is the Flask API running at http://127.0.0.1:5000 ?");
    } finally {
        fetchBtn.textContent = "Fetch Data";
        loadingIndicator.style.display = 'none';
    }
});

document.getElementById('dcf-form').addEventListener('submit', function(e) {
    // Prevent the default form submission (which reloads the page)
    e.preventDefault();

    // 1. Grab numeric values from inputs
    const fcf = parseFloat(document.getElementById('fcf').value);
    // Convert percentage inputs into decimals for math (e.g., 5% -> 0.05)
    const growthRate = parseFloat(document.getElementById('growth-rate').value) / 100;
    const terminalRate = parseFloat(document.getElementById('terminal-rate').value) / 100;
    const discountRate = parseFloat(document.getElementById('discount-rate').value) / 100;
    const shares = parseFloat(document.getElementById('shares').value);

    // Edge Case Check: Discount rate must be strictly greater than terminal growth rate
    // Otherwise, the terminal value math will result in an infinite or negative number.
    if (discountRate <= terminalRate) {
        alert("Discount Rate must be higher than the Terminal Growth Rate.");
        return;
    }

    // 2. Calculate Present Value (PV) of projected Free Cash Flows for Years 1-5
    let totalPvFcf = 0;
    let projectedFcf = fcf;
    
    for (let year = 1; year <= 5; year++) {
        // Grow the cash flow by the projected growth rate
        projectedFcf *= (1 + growthRate);
        // Discount it back to today's value using the discount rate
        let pv = projectedFcf / Math.pow((1 + discountRate), year);
        totalPvFcf += pv;
    }

    // 3. Calculate Terminal Value at year 5, and discount it back to Present Value
    // First, find the expected Free Cash Flow at Year 6
    const year6Fcf = projectedFcf * (1 + terminalRate);
    // Gordon Growth Model to find Terminal Value at Year 5
    const terminalValue = year6Fcf / (discountRate - terminalRate);
    // Discount the Terminal Value back 5 years to Present Value
    const pvTerminalValue = terminalValue / Math.pow((1 + discountRate), 5);

    // 4. Calculate Total Enterprise Value
    const totalEnterpriseValue = totalPvFcf + pvTerminalValue;

    // 5. Calculate Intrinsic Value per Share
    const intrinsicValuePerShare = totalEnterpriseValue / shares;

    // 6. Format as currency and update HTML elements
    const formatter = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });

    document.getElementById('final-value').textContent = `Intrinsic Value per Share: ${formatter.format(intrinsicValuePerShare)}`;
    document.getElementById('enterprise-value').textContent = `Total Enterprise Value: ${formatter.format(totalEnterpriseValue)}B`;
    document.getElementById('projected-cash-flow').textContent = `Projected 5-Year Cash Flow: ${formatter.format(projectedFcf)}B`;
});