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