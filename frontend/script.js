document.getElementById('fetch-data-btn').addEventListener('click', async function() {
    const ticker = document.getElementById('ticker').value.trim();

    if (!ticker) {
        alert("Please enter a ticker symbol.");
        return;
    }

    const fetchBtn = document.getElementById('fetch-data-btn');
    fetchBtn.textContent = "Auto-Filling...";
    const loadingIndicator = document.getElementById('loading-indicator');
    loadingIndicator.style.display = 'block';

    try {
        // Fetch data from the Flask API
        const response = await fetch(`http://127.0.0.1:5000/api/financials/${ticker}?freq=annual&years=4`);
        const data = await response.json();

        // Log data to inspect the structure in Developer Tools
        console.log("API Response Data:", data);

        if (data.error) {
            console.log("API Error:", data.error);
            alert(`Error fetching data: ${data.error}`);
            return;
        }

        // Extract Free Cash Flow and convert from raw dollars to Billions
        const fcfData = data["Free Cash Flow"];
        if (fcfData) {
            const sortedYears = Object.keys(fcfData).sort().reverse();
            const latestYear = sortedYears[0];
            let rawFcf = parseFloat(String(fcfData[latestYear]).replace(/[^0-9.-]+/g, ""));
            document.getElementById('fcf').value = (rawFcf / 1e9).toFixed(2);
            
            // Calculate 3-Year CAGR for Growth Rate
            if (sortedYears.length >= 4) {
                const pastYear = sortedYears[3]; // 3 years prior
                let pastFcf = parseFloat(String(fcfData[pastYear]).replace(/[^0-9.-]+/g, ""));
                
                if (pastFcf <= 0) {
                    document.getElementById('growth-rate').value = "0.00";
                } else {
                    let cagr = (Math.pow((rawFcf / pastFcf), 1 / 3) - 1) * 100;
                    document.getElementById('growth-rate').value = isNaN(cagr) ? "0.00" : cagr.toFixed(2);
                }
            }
        }

        // Extract Shares Outstanding and convert from raw shares to Millions
        const sharesData = data["Basic Shares Outstanding"] || data["Diluted Shares Outstanding"];
        if (sharesData) {
            const latestYear = Object.keys(sharesData).sort().reverse()[0];
            let rawShares = parseFloat(String(sharesData[latestYear]).replace(/[^0-9.-]+/g, ""));
            document.getElementById('shares').value = (rawShares / 1e6).toFixed(2);
        }

    } catch (error) {
        console.log("Error fetching data:", error);
        alert("Failed to fetch data. Is the Flask API running at http://127.0.0.1:5000 ?");
    } finally {
        fetchBtn.textContent = "Auto-Fill from SEC";
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
    const marginOfSafety = parseFloat(document.getElementById('margin-of-safety').value);

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
    const intrinsicValuePerShare = (totalEnterpriseValue * 1000) / shares;

    // 6. Calculate Target Buy Price with Margin of Safety
    const targetBuyPrice = intrinsicValuePerShare * (1 - (marginOfSafety / 100));

    // 7. Format as currency and update HTML elements
    const formatter = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });

    document.getElementById('final-value').textContent = `Intrinsic Value per Share: ${formatter.format(intrinsicValuePerShare)}`;
    document.getElementById('target-buy-price').textContent = `Target Buy Price: ${formatter.format(targetBuyPrice)}`;
    document.getElementById('enterprise-value').textContent = `Total Enterprise Value: ${formatter.format(totalEnterpriseValue)}B`;
    document.getElementById('projected-cash-flow').textContent = `Projected 5-Year Cash Flow: ${formatter.format(projectedFcf)}B`;
});