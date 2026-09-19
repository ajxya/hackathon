# ED Resource Allocator (Hackathon MVP)

A Streamlit dashboard for emergency department resource allocation: predicts
patient admission likelihood, and helps balance nurse staffing and bed flow.

## Run it

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Pages

- **Dashboard** — census, wait times, alert banners, arrival/wait trends.
- **Patient Triage & Risk** — vitals form → ML admission probability + acuity (ESI-style) score.
- **Staff Allocator** — editable nurse roster, zone load chart, auto-balance action.
- **Bed Flow Tracker** — room grid by status, boarding bottleneck & cleaning turnaround tables.

## Notes

All data is synthetic and regenerated per session. The triage model is a
`scikit-learn` LogisticRegression trained on synthetic vitals — a stand-in for
a real model trained on hospital data, not a clinical tool.
