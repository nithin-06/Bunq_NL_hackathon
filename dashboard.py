"""
dashboard.py — Generate a financial dashboard HTML file from CSV data.

Reads mock_bank CSVs and produces a self-contained HTML page with:
  - Account summary cards (checking, savings, portfolio)
  - Expense breakdown pie chart by category
  - Portfolio holdings bar chart
  - Full transaction history table

Run:
    python dashboard.py             # generates dashboard.html, opens in browser
    python dashboard.py --no-open   # just generate the file
"""

import json
import sys
import webbrowser
from pathlib import Path
from collections import defaultdict

import mock_bank

OUT_PATH = Path(__file__).parent / "dashboard.html"


def _build_data():
    balances    = mock_bank.get_balance()
    portfolio   = mock_bank.get_portfolio()
    all_tx      = mock_bank.get_all_transactions()

    # Expense totals by category
    category_totals = defaultdict(float)
    for tx in all_tx:
        if tx.get("type") == "expense":
            cat    = tx.get("category", "other")
            amount = float(tx.get("amount", 0))
            category_totals[cat] += amount

    # Timeline: checking balance over time (last 30 transactions)
    timeline = []
    for tx in all_tx[-30:]:
        bal = tx.get("balance_after", "")
        if bal != "":
            timeline.append({
                "ts":  tx["timestamp"][:10],
                "bal": float(bal),
            })

    # Portfolio table
    portfolio_rows = []
    for instrument, d in portfolio.items():
        portfolio_rows.append({
            "name":  instrument,
            "units": d["units"],
            "price": d["price_per_unit"],
            "value": round(d["units"] * d["price_per_unit"], 2),
        })

    return {
        "balances":        balances,
        "category_totals": dict(category_totals),
        "timeline":        timeline,
        "portfolio":       portfolio_rows,
        "transactions":    all_tx[-20:],  # last 20 for the table
    }


def generate_html(data: dict) -> str:
    balances_json  = json.dumps(data["balances"])
    categories_json = json.dumps(data["category_totals"])
    timeline_json  = json.dumps(data["timeline"])
    portfolio_json = json.dumps(data["portfolio"])
    tx_json        = json.dumps(data["transactions"])

    total_expenses = sum(data["category_totals"].values())
    net_worth = (
        data["balances"]["checking"]
        + data["balances"]["savings"]
        + data["balances"]["investments"]
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Financial Dashboard</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600&family=DM+Mono:wght@400;500&display=swap');

  :root {{
    --bg:       #0e0f11;
    --surface:  #16181c;
    --card:     #1e2026;
    --border:   rgba(255,255,255,0.07);
    --text:     #e8e9eb;
    --muted:    #7a7d85;
    --accent:   #6c8eff;
    --green:    #3ecf8e;
    --amber:    #f5a623;
    --red:      #f06060;
    --pink:     #e879a0;
    --teal:     #38c4c4;
    --purple:   #a78bfa;
  }}

  * {{ box-sizing: border-box; margin: 0; padding: 0; }}

  body {{
    font-family: 'DM Sans', sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    padding: 2rem;
  }}

  h1 {{
    font-size: 1.4rem;
    font-weight: 500;
    letter-spacing: -0.02em;
    color: var(--text);
    margin-bottom: 0.2rem;
  }}

  .subtitle {{
    font-size: 0.8rem;
    color: var(--muted);
    margin-bottom: 2rem;
  }}

  .grid-4 {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
    margin-bottom: 1.5rem;
  }}

  .grid-2 {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    margin-bottom: 1.5rem;
  }}

  .card {{
    background: var(--card);
    border: 0.5px solid var(--border);
    border-radius: 14px;
    padding: 1.2rem 1.4rem;
  }}

  .stat-label {{
    font-size: 0.7rem;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--muted);
    margin-bottom: 0.5rem;
  }}

  .stat-value {{
    font-size: 1.6rem;
    font-weight: 600;
    letter-spacing: -0.03em;
    color: var(--text);
    font-variant-numeric: tabular-nums;
  }}

  .stat-badge {{
    display: inline-block;
    font-size: 0.7rem;
    font-weight: 500;
    padding: 2px 8px;
    border-radius: 20px;
    margin-top: 0.4rem;
  }}

  .badge-green {{ background: rgba(62,207,142,0.12); color: var(--green); }}
  .badge-amber {{ background: rgba(245,166,35,0.12); color: var(--amber); }}
  .badge-red   {{ background: rgba(240,96,96,0.12);  color: var(--red);   }}

  .card-title {{
    font-size: 0.8rem;
    font-weight: 500;
    color: var(--muted);
    margin-bottom: 1.2rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }}

  .chart-wrap {{
    position: relative;
  }}

  .pie-wrap {{
    position: relative;
    height: 240px;
    display: flex;
    align-items: center;
    justify-content: center;
  }}

  .legend {{
    margin-top: 1rem;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }}

  .legend-row {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-size: 0.78rem;
  }}

  .legend-left {{
    display: flex;
    align-items: center;
    gap: 8px;
    color: var(--muted);
  }}

  .legend-dot {{
    width: 8px;
    height: 8px;
    border-radius: 2px;
    flex-shrink: 0;
  }}

  .legend-amount {{
    font-family: 'DM Mono', monospace;
    font-size: 0.75rem;
    color: var(--text);
  }}

  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.78rem;
  }}

  thead th {{
    color: var(--muted);
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-size: 0.65rem;
    padding: 0 0 0.8rem 0;
    text-align: left;
    border-bottom: 0.5px solid var(--border);
  }}

  tbody td {{
    padding: 0.65rem 0;
    border-bottom: 0.5px solid var(--border);
    color: var(--text);
    font-variant-numeric: tabular-nums;
  }}

  tbody tr:last-child td {{
    border-bottom: none;
  }}

  .type-badge {{
    font-size: 0.65rem;
    font-weight: 500;
    padding: 2px 7px;
    border-radius: 20px;
  }}

  .type-expense    {{ background: rgba(240,96,96,0.12);  color: var(--red);    }}
  .type-investment {{ background: rgba(108,142,255,0.12); color: var(--accent); }}
  .type-savings    {{ background: rgba(62,207,142,0.12); color: var(--green);  }}

  .mono {{ font-family: 'DM Mono', monospace; font-size: 0.75rem; }}

  .net-worth-row {{
    display: flex;
    align-items: baseline;
    gap: 0.5rem;
    margin-bottom: 1.5rem;
  }}

  .net-label {{ font-size: 0.8rem; color: var(--muted); }}
  .net-value {{ font-size: 2.4rem; font-weight: 600; letter-spacing: -0.04em; color: var(--text); }}

  @media (max-width: 700px) {{
    .grid-4 {{ grid-template-columns: repeat(2, 1fr); }}
    .grid-2 {{ grid-template-columns: 1fr; }}
  }}
</style>
</head>
<body>

<h1>Financial Overview</h1>
<div class="subtitle" id="ts"></div>

<div class="grid-4">
  <div class="card">
    <div class="stat-label">Net worth</div>
    <div class="stat-value" id="net-worth">€0</div>
    <div class="stat-badge badge-green">Total assets</div>
  </div>
  <div class="card">
    <div class="stat-label">Checking</div>
    <div class="stat-value" id="checking">€0</div>
    <div class="stat-badge badge-amber">Available</div>
  </div>
  <div class="card">
    <div class="stat-label">Savings</div>
    <div class="stat-value" id="savings">€0</div>
    <div class="stat-badge badge-green">Safe</div>
  </div>
  <div class="card">
    <div class="stat-label">Investments</div>
    <div class="stat-value" id="investments">€0</div>
    <div class="stat-badge badge-amber">Portfolio</div>
  </div>
</div>

<div class="grid-2">
  <!-- Expense Pie -->
  <div class="card">
    <div class="card-title">Spending by category</div>
    <div class="pie-wrap">
      <canvas id="pieChart" role="img" aria-label="Pie chart showing spending breakdown by category"></canvas>
    </div>
    <div class="legend" id="pie-legend"></div>
  </div>

  <!-- Portfolio Bar -->
  <div class="card">
    <div class="card-title">Portfolio holdings</div>
    <div class="chart-wrap" style="height: 240px; position: relative;">
      <canvas id="barChart" role="img" aria-label="Bar chart showing investment portfolio values"></canvas>
    </div>
    <div class="legend" id="bar-legend" style="margin-top: 1rem;"></div>
  </div>
</div>

<!-- Transaction Table -->
<div class="card">
  <div class="card-title">Recent transactions</div>
  <table>
    <thead>
      <tr>
        <th>Date</th>
        <th>Type</th>
        <th>Merchant / Instrument</th>
        <th>Category</th>
        <th style="text-align:right">Amount</th>
        <th style="text-align:right">Balance</th>
      </tr>
    </thead>
    <tbody id="tx-body"></tbody>
  </table>
</div>

<script>
const balances      = {balances_json};
const categoryData  = {categories_json};
const portfolioData = {portfolio_json};
const transactions  = {tx_json};

document.getElementById('ts').textContent = 'Updated ' + new Date().toLocaleString();

const fmt = v => '€' + Number(v).toLocaleString('nl-NL', {{minimumFractionDigits:2, maximumFractionDigits:2}});

document.getElementById('checking').textContent    = fmt(balances.checking);
document.getElementById('savings').textContent     = fmt(balances.savings);
document.getElementById('investments').textContent = fmt(balances.investments);
const netWorth = balances.checking + balances.savings + balances.investments;
document.getElementById('net-worth').textContent   = fmt(netWorth);

const PIE_COLORS = ['#6c8eff','#3ecf8e','#f5a623','#e879a0','#38c4c4','#a78bfa','#f06060'];
const BAR_COLORS = ['#6c8eff','#3ecf8e','#f5a623','#e879a0'];

// --- Pie chart ---
const cats   = Object.keys(categoryData);
const vals   = Object.values(categoryData);
const total  = vals.reduce((a,b) => a+b, 0);

if (cats.length > 0) {{
  new Chart(document.getElementById('pieChart'), {{
    type: 'doughnut',
    data: {{
      labels: cats,
      datasets: [{{
        data: vals,
        backgroundColor: PIE_COLORS.slice(0, cats.length),
        borderColor: '#1e2026',
        borderWidth: 3,
        hoverOffset: 6,
      }}]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      cutout: '62%',
      plugins: {{ legend: {{ display: false }}, tooltip: {{
        callbacks: {{
          label: ctx => ` €${{ctx.parsed.toFixed(2)}} (${{(ctx.parsed/total*100).toFixed(1)}}%)`
        }}
      }} }}
    }}
  }});

  const legend = document.getElementById('pie-legend');
  cats.forEach((cat, i) => {{
    const pct = total > 0 ? (vals[i]/total*100).toFixed(1) : 0;
    legend.innerHTML += `<div class="legend-row">
      <div class="legend-left">
        <div class="legend-dot" style="background:${{PIE_COLORS[i]}}"></div>
        <span>${{cat}}</span>
      </div>
      <span class="legend-amount">€${{vals[i].toFixed(2)}} &nbsp; ${{pct}}%</span>
    </div>`;
  }});
}} else {{
  document.getElementById('pieChart').parentElement.innerHTML =
    '<div style="color:var(--muted);font-size:.8rem;text-align:center;padding:3rem 0;">No expense data yet.<br>Scan a receipt to get started.</div>';
}}

// --- Portfolio bar chart ---
const pNames  = portfolioData.map(r => r.name);
const pValues = portfolioData.map(r => r.value);

if (pNames.length > 0) {{
  new Chart(document.getElementById('barChart'), {{
    type: 'bar',
    data: {{
      labels: pNames,
      datasets: [{{
        label: 'Value (€)',
        data: pValues,
        backgroundColor: BAR_COLORS.slice(0, pNames.length),
        borderRadius: 6,
        borderSkipped: false,
      }}]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      plugins: {{ legend: {{ display: false }} }},
      scales: {{
        x: {{ ticks: {{ color: '#7a7d85', font: {{ size: 11 }} }}, grid: {{ color: 'rgba(255,255,255,0.04)' }} }},
        y: {{ ticks: {{ color: '#7a7d85', font: {{ size: 11 }}, callback: v => '€'+v }} , grid: {{ color: 'rgba(255,255,255,0.04)' }} }}
      }}
    }}
  }});

  const bLegend = document.getElementById('bar-legend');
  portfolioData.forEach((r, i) => {{
    bLegend.innerHTML += `<div class="legend-row">
      <div class="legend-left">
        <div class="legend-dot" style="background:${{BAR_COLORS[i]}}"></div>
        <span>${{r.name}}</span>
      </div>
      <span class="legend-amount">${{r.units}} units &nbsp; €${{r.value.toFixed(2)}}</span>
    </div>`;
  }});
}}

// --- Transaction table ---
const tbody = document.getElementById('tx-body');
const txType = {{ expense:'expense', investment:'investment', savings_transfer:'savings' }};
[...transactions].reverse().forEach(tx => {{
  const typeKey = txType[tx.type] || 'savings';
  const merchant = tx.merchant || tx.instrument || '—';
  const bal = tx.balance_after !== '' ? fmt(parseFloat(tx.balance_after)) : '—';
  tbody.innerHTML += `<tr>
    <td class="mono">${{tx.timestamp.slice(0,10)}}</td>
    <td><span class="type-badge type-${{typeKey}}">${{tx.type.replace('_',' ')}}</span></td>
    <td>${{merchant}}</td>
    <td style="color:var(--muted)">${{tx.category || '—'}}</td>
    <td style="text-align:right" class="mono">€${{parseFloat(tx.amount||0).toFixed(2)}}</td>
    <td style="text-align:right" class="mono">${{bal}}</td>
  </tr>`;
}});
</script>
</body>
</html>"""


def main():
    data = _build_data()
    html = generate_html(data)
    OUT_PATH.write_text(html, encoding="utf-8")
    print(f"[DASHBOARD] Generated → {OUT_PATH}")

    if "--no-open" not in sys.argv:
        webbrowser.open(f"file://{OUT_PATH.resolve()}")
        print("[DASHBOARD] Opened in browser.")


if __name__ == "__main__":
    main()