from flask import Flask, render_template_string, request
import pandas as pd
import requests
from io import StringIO
from itertools import combinations

app = Flask(__name__)

GOOGLE_SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vSwiZjQ1dK9Wxe_GYcoeELm3-nXy-xGEG4WSbCXuk-JClxcF9kEseWhAovCsWwx_8NkgoSryDNKZATO/pub?output=csv"
SALARY_CAP = 50000

# ------------------ HTML Templates ------------------

PLAYER_HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title>Player Pool</title>
<style>
body { font-family: 'Segoe UI', Tahoma, sans-serif; margin: 20px; background: #f5f6fa; color: #2c3e50; }
h1 { color: #2c3e50; margin-bottom: 10px; }
.card { background: white; border-radius: 8px; padding: 20px; box-shadow: 0 2px 6px rgba(0,0,0,0.1); margin-bottom: 20px; }
table { border-collapse: collapse; width: 100%; margin-top: 10px; }
th, td { border: 1px solid #ddd; padding: 8px; text-align: center; }
th { background-color: #34495e; color: white; }
tr:nth-child(even) { background-color: #f9f9f9; }
tr:hover { background-color: #ecf0f1; }
form { margin-top: 15px; }
input[type=checkbox], input[type=radio], input[type=number] { transform: scale(1.1); margin: 2px; }
button { padding: 10px 16px; background-color: #27ae60; color: white; border: none; border-radius: 5px; cursor: pointer; font-size: 14px; }
button:hover { background-color: #2ecc71; }
label { font-weight: 500; margin-right: 10px; }
select { font-size: 14px; padding: 4px 8px; margin-left: 10px; }
</style>
</head>
<body>
<div class="card">
<h1>NFL Showdown Player Pool</h1>
<p>Total Players: {{ players|length }}</p>

<form method="get" action="/lineups">
<label>Number of Lineups:</label>
<input type="number" name="count" value="1" min="1" max="10">

<label>Game Script:</label>
<select name="script">
  <option value="">Balanced</option>
  <option value="run">Run Heavy (RB Captain)</option>
  <option value="pass">Pass Heavy (QB/WR/TE Captain)</option>
</select>

<br><br>
<table>
<thead>
<tr>
<th>Name</th>
<th>Team</th>
<th>POS</th>
<th>Salary</th>
<th>Projected Points</th>
<th>CPT Lock</th>
<th>FLEX Lock</th>
<th>Exclude</th>
</tr>
</thead>
<tbody>
{% for p in players %}
<tr>
<td>{{ p.Name }}</td>
<td>{{ p.Team }}</td>
<td>{{ p.POS }}</td>
<td>${{ "{:,.0f}".format(p.Salary) }}</td>
<td>{{ "%.2f"|format(p.Proj) }}</td>
<td><input type="radio" name="lock_cpt" value="{{ p.Name }}"></td>
<td><input type="checkbox" name="lock_flex" value="{{ p.Name }}"></td>
<td><input type="checkbox" name="exclude" value="{{ p.Name }}"></td>
</tr>
{% endfor %}
</tbody>
</table>
<br>
<button type="submit">⚡ Generate Lineups</button>
</form>
</div>
</body>
</html>
"""

LINEUP_HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title>Generated Lineups</title>
<style>
body { font-family: 'Segoe UI', Tahoma, sans-serif; margin: 20px; background: #f5f6fa; color: #2c3e50; }
h1 { color: #2c3e50; margin-bottom: 20px; }
.card { background: white; border-radius: 10px; padding: 20px; box-shadow: 0 3px 8px rgba(0,0,0,0.1); margin-bottom: 25px; transition: transform 0.1s ease-in-out; }
.card:hover { transform: translateY(-2px); }
table { border-collapse: collapse; width: 100%; margin-top: 10px; }
th, td { border: 1px solid #ddd; padding: 8px; text-align: center; }
th { background-color: #34495e; color: white; }
.role-CPT { color: #e74c3c; font-weight: bold; }
.role-FLEX { color: #2980b9; }
button { padding: 10px 16px; background-color: #3498db; color: white; border: none; border-radius: 5px; cursor: pointer; font-size: 14px; }
button:hover { background-color: #2980b9; }
</style>
</head>
<body>
<h1>Generated Lineups</h1>

{% if error %}
<p style="color:red;">{{ error }}</p>
{% endif %}

{% for lu in lineups %}
<div class="card">
<h2>Lineup {{ loop.index }}</h2>
<p><strong>Salary:</strong> ${{ "{:,.0f}".format(lu.Salary) }} | 
<strong>Projected:</strong> {{ "%.2f"|format(lu.Projected) }}</p>
<table>
<tr><th>Role</th><th>Name</th><th>Team</th><th>Salary</th><th>Proj</th></tr>
{% for p in lu.players %}
<tr>
<td class="role-{{ p.Role }}">{{ p.Role }}</td>
<td>{{ p.Name }}</td>
<td>{{ p.Team }}</td>
<td>${{ "{:,.0f}".format(p.Salary) }}</td>
<td>{{ "%.2f"|format(p.Proj) }}</td>
</tr>
{% endfor %}
</table>
</div>
{% endfor %}

<form action="/" method="get">
    <button type="submit">⬅ Back to Player Pool</button>
</form>

</body>
</html>
"""

# ------------------ Data Cleaning ------------------
def clean_data(df):
    df = df.dropna(how='all')
    df.columns = df.columns.str.strip().str.upper()
    name_col = next((c for c in df.columns if "PLAYER" in c), None)
    salary_col = next((c for c in df.columns if "SALARY" in c), None)
    proj_col = next((c for c in df.columns if "FINAL POINTS" in c), None)
    team_col = next((c for c in df.columns if "TEAM" in c.upper()), None)
    pos_col = next((c for c in df.columns if c == "POS"), None)

    df = df.rename(columns={name_col: "Name", salary_col: "Salary", proj_col: "Proj", team_col: "Team", pos_col: "POS"})
    df["Salary"] = df["Salary"].astype(str).str.replace(r'[\$,]', '', regex=True)
    df = df[df["Salary"].str.replace('.', '', 1).str.isnumeric()]
    df["Salary"] = df["Salary"].astype(float)
    df["Proj"] = pd.to_numeric(df["Proj"], errors="coerce")
    df = df.dropna(subset=["Name", "Salary", "Proj", "POS"]).drop_duplicates(subset=["Name"])
    return df[["Name", "Team", "POS", "Salary", "Proj"]]

# ------------------ Lineup Generator ------------------
def generate_all_lineups(df, lock_cpt=None, lock_flex=[], exclude=[], max_lineups=5, script=None):
    players = df.copy()
    if exclude:
        players = players[~players["Name"].isin(exclude)]
    if len(players) < 6:
        return []

    # Filter CPT pool
    if lock_cpt:
        cpt_pool = players[players["Name"] == lock_cpt]
    else:
        if script == "run":
            cpt_pool = players[players["POS"] == "RB"]
        elif script == "pass":
            cpt_pool = players[players["POS"].isin(["QB", "WR", "TE"])]
        else:
            cpt_pool = players

    cpt_pool = cpt_pool.sort_values(by="Salary", ascending=False)

    flex_pool = players
    all_lineups = []
    flex_combos = combinations(flex_pool["Name"], 5)

    for flex_names in flex_combos:
        if not all(f in flex_names for f in lock_flex):
            continue

        for cpt_row in cpt_pool.itertuples():
            if cpt_row.Name in flex_names:
                continue

            lineup = [{"Name": cpt_row.Name, "Role": "CPT", "Salary": round(cpt_row.Salary * 1.5, -2),
                       "Proj": cpt_row.Proj * 1.5, "Team": cpt_row.Team}]
            for name in flex_names:
                row = players[players["Name"] == name].iloc[0]
                lineup.append({"Name": row.Name, "Role": "FLEX", "Salary": row.Salary,
                               "Proj": row.Proj, "Team": row.Team})

            total_salary = sum(p["Salary"] for p in lineup)
            if total_salary <= SALARY_CAP:
                total_proj = sum(p["Proj"] for p in lineup)
                all_lineups.append({"players": lineup, "Salary": total_salary, "Projected": total_proj})

            if len(all_lineups) >= max_lineups:
                break
        if len(all_lineups) >= max_lineups:
            break

    return sorted(all_lineups, key=lambda x: x["Projected"], reverse=True)[:max_lineups]

# ------------------ Routes ------------------
@app.route('/')
def player_pool():
    try:
        df = pd.read_csv(StringIO(requests.get(GOOGLE_SHEET_CSV_URL, timeout=10).text))
        df = clean_data(df)
        players = df.to_dict(orient="records")
        return render_template_string(PLAYER_HTML_TEMPLATE, players=players)
    except Exception as e:
        return f"<p>Error loading player pool: {e}</p>"

@app.route('/lineups')
def generate_lineups():
    try:
        df = pd.read_csv(StringIO(requests.get(GOOGLE_SHEET_CSV_URL, timeout=10).text))
        df = clean_data(df)

        lock_cpt = request.args.get("lock_cpt")
        lock_flex = request.args.getlist("lock_flex")
        exclude = request.args.getlist("exclude")
        count = int(request.args.get("count", 1))
        script = request.args.get("script", "").lower()

        count = max(1, min(count, 10))
        lineups = generate_all_lineups(df, lock_cpt, lock_flex, exclude, count, script)

        if not lineups:
            return render_template_string(LINEUP_HTML_TEMPLATE, lineups=[], error="Could not generate lineups with these selections.")

        return render_template_string(LINEUP_HTML_TEMPLATE, lineups=lineups, error=None)

    except Exception as e:
        return f"<p>Error generating lineups: {e}</p>"

if __name__ == '__main__':
    app.run(debug=True)
