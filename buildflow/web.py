from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path
from threading import Lock

from flask import Flask, jsonify, render_template, request, send_file
from io import BytesIO

from .agent import ProjectAgent
from .costs import CostError
from .cpm import CpmError
from .exporter import export_xlsx
from .ingestion import read_boq
from .models import AnalysisResult, config_from_dict
from .normalization import review_document
from .normalization_schema import SCHEMA, MAX_JSON_BYTES
from .normalized_analysis import ReviewBlocked, analyze_reviewed
from .pipeline import run_pipeline
from .resolutions import review_input
from .review_schema import MAX_REVIEW_BYTES


ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = ROOT / "data" / "samples"
SAMPLES = {
    "stp": {"label": "550 KLD STP", "file": "stp_boq.csv", "typology": "stp_tank", "name": "550 KLD Sewage Treatment Plant, Karnal", "start_date": "2026-04-25", "contract_duration_days": 150, "structure_count": 1, "data_provenance": "sanitised_demo"},
    "mep": {"label": "Narsi Village MEP", "file": "mep_boq.csv", "typology": "linear_mep", "name": "Road redevelopment MEP works, Narsi Village", "start_date": "2026-04-25", "contract_duration_days": 130, "structure_count": 1, "data_provenance": "sanitised_demo"},
    "rwh": {"label": "Two-tank RWH", "file": "rwh_boq.csv", "typology": "rwh", "name": "Rain Water Harvesting System, Narsi Village", "start_date": "2026-04-25", "contract_duration_days": 90, "structure_count": 2, "data_provenance": "sanitised_demo"},
}


class ResultStore:
    def __init__(self, limit: int = 20):
        self.limit = limit
        self.data: OrderedDict[str, AnalysisResult] = OrderedDict()
        self.lock = Lock()

    def put(self, result: AnalysisResult) -> None:
        with self.lock:
            self.data[result.job_id] = result
            self.data.move_to_end(result.job_id)
            while len(self.data) > self.limit:
                self.data.popitem(last=False)

    def get(self, job_id: str) -> AnalysisResult | None:
        with self.lock:
            return self.data.get(job_id)


def create_app() -> Flask:
    app = Flask(__name__, template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))
    app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
    store = ResultStore()
    agent = ProjectAgent()

    @app.get("/workbench")
    def index():
        return render_template("index.html")

    @app.get('/')
    @app.get('/normalize')
    def normalize_page():
        prompt=(ROOT/'docs'/'normalization'/'CHAT_NORMALIZER_PROMPT.md').read_text(encoding='utf-8')
        return render_template('normalize.html',normalizer_prompt=prompt)

    @app.get('/api/normalization/prompt')
    def normalization_prompt():
        return send_file(ROOT/'docs'/'normalization'/'CHAT_NORMALIZER_PROMPT.md',as_attachment=True)

    @app.get('/api/normalization/schema')
    def normalization_schema():
        return jsonify(SCHEMA)

    def uploaded_review():
        normalized=request.files.get('normalized')
        originals=request.files.getlist('originals')
        if not normalized or not originals:
            raise ValueError('Upload normalized JSON and the original Excel workbook(s)')
        sources={}
        for original in originals:
            if not original.filename or original.filename in sources:
                raise ValueError('Original workbook names must be present and unique')
            sources[original.filename]=original.read()
        return review_input(normalized.read(MAX_REVIEW_BYTES+1),sources)

    @app.post('/api/normalization/review')
    def normalization_review():
        try:
            return jsonify(uploaded_review())
        except (ValueError, TypeError) as exc:
            return jsonify(error=str(exc)),400

    @app.post('/api/normalization/save')
    def normalization_save():
        try:
            return jsonify(uploaded_review()['reviewed_document'])
        except (ValueError, TypeError) as exc:
            return jsonify(error=str(exc)),400

    @app.post('/api/normalization/analyze')
    def normalization_analyze():
        try:
            result=analyze_reviewed(uploaded_review(),request.form.to_dict())
            store.put(result)
            return jsonify(result.to_dict())
        except ReviewBlocked as exc:
            return jsonify(error=str(exc)),422
        except (ValueError, TypeError) as exc:
            return jsonify(error=str(exc)),400

    @app.get("/schedule")
    def schedule_page():
        return render_template("schedule.html")

    def _schedule_response(data, body):
        try:
            result, report = run_pipeline(
                data, body.get("start_date") or "2026-10-01",
                overrides=body.get("overrides") or {},
                workweek_days=int(body.get("workweek_days") or 6),
                holidays=body.get("holidays") or [],
                cashflow=body.get("cashflow") or None)
        except (CpmError, CostError, ValueError, KeyError, TypeError) as exc:
            return jsonify(error=str(exc)), 400
        result["_reports"] = report
        return jsonify(result)

    @app.get("/api/schedule/demo")
    def schedule_demo():
        data = json.loads((ROOT / "data" / "rwh_activities.json").read_text())
        return _schedule_response(data, {"start_date": request.args.get("start")})

    @app.get("/api/schedule/demo-input")
    def schedule_demo_input():
        return jsonify(json.loads((ROOT / "data" / "rwh_activities.json").read_text()))

    @app.get("/api/schedule/demo-two-tank")
    def schedule_demo_two_tank():
        return jsonify({
            "input": json.loads((ROOT / "data" / "rwh_two_tank_activities.json").read_text()),
            "overrides": json.loads((ROOT / "data" / "rwh_gang_overrides.json").read_text()),
            "start_date": "2026-04-25"})

    @app.post("/api/schedule/run")
    def schedule_run():
        body = request.get_json(silent=True) or {}
        if "activities_json" not in body:
            return jsonify(error="send {\"activities_json\": <precedence JSON>, \"start_date\": ...}"), 400
        return _schedule_response(body["activities_json"], body)

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "service": "BuildFlow", "version": "0.1.0"})

    @app.get("/api/samples")
    def samples():
        return jsonify({key: {field: value for field, value in sample.items() if field != "file"} for key, sample in SAMPLES.items()})

    @app.post("/api/analyze")
    def analyze():
        try:
            form = request.form.to_dict()
            sample_id = form.pop("sample_id", "")
            if sample_id:
                if sample_id not in SAMPLES:
                    return jsonify({"error": "Unknown sample project"}), 400
                sample = SAMPLES[sample_id]
                for key in ("typology", "name", "start_date", "contract_duration_days", "structure_count", "data_provenance"):
                    if not form.get(key):
                        form[key] = sample[key]
                items = read_boq(SAMPLE_DIR / sample["file"])
            else:
                upload = request.files.get("file")
                if not upload or not upload.filename:
                    return jsonify({"error": "Upload a CSV/XLSX BOQ or select a sample"}), 400
                items = read_boq(upload.stream, upload.filename)
            result = agent.run(items, config_from_dict(form))
            store.put(result)
            return jsonify(result.to_dict())
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            app.logger.exception("Analysis failed")
            return jsonify({"error": f"Analysis failed: {exc}"}), 500

    @app.post("/api/jobs/<job_id>/replan")
    def replan(job_id: str):
        existing = store.get(job_id)
        if not existing:
            return jsonify({"error": "Analysis job not found; run the analysis again"}), 404
        try:
            payload = request.get_json(force=True) or {}
            updated = agent.replan(existing, payload.get("overrides", []))
            store.put(updated)
            return jsonify(updated.to_dict())
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.get("/api/jobs/<job_id>/export.xlsx")
    def export(job_id: str):
        result = store.get(job_id)
        if not result:
            return jsonify({"error": "Analysis job not found; run the analysis again"}), 404
        output = BytesIO(export_xlsx(result))
        safe_name = "".join(character if character.isalnum() else "_" for character in result.project.name).strip("_")[:60]
        return send_file(output, as_attachment=True, download_name=f"{safe_name or 'BuildFlow'}_analysis.xlsx", mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @app.errorhandler(413)
    def too_large(_):
        return jsonify({"error": "BOQ file exceeds the 20 MB limit"}), 413

    return app
