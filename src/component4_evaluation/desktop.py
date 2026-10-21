"""Native Tk viewer. Workers execute real missions; Tk only renders safe reports."""

import json
from math import hypot, isclose, isfinite
from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .harness import (
    LIMITATIONS, SCENARIOS, EvaluationError, compare_runs, export_result, run_evaluation,
)


def readable(value):
    return "NOT AVAILABLE" if value is None else str(value)


def metric_text(value):
    """Round-trip float precision; missing/nonfinite/invalid values stay unknown."""
    if type(value) not in (int, float) or not isfinite(value) or value < 0:
        return "NOT AVAILABLE"
    return str(value) if type(value) is int else repr(value)


def recommended_path(result):
    """Presentation guard only: never select a route or grant release authority.

    Consume the independent oracle's recommendation, match FULL edge keys, and
    withhold green styling if its projected metrics/path are unavailable or differ.
    """
    if (result is None or result.assessment.get("verdict") != "PASS"
            or result.assessment.get("task_completed_correctly") is not True):
        return None, []
    route = next((r for r in result.assessment["routes"]
                  if r["route_id"] == result.assessment.get("recommendation")), None)
    if route is None or not route.get("path"):
        return None, []
    edges = {(e["u"], e["v"], e["key"]): e for e in result.graph["edges"]}
    keys = [tuple(key) for key in route["path"]]
    if len(edges) != len(result.graph["edges"]) or len(keys) != len(set(keys)) or any(k not in edges for k in keys):
        return None, []
    path = [edges[key] for key in keys]
    if (path[0]["u"] != result.mission["origin"] or path[-1]["v"] != result.mission["destination"]
            or any(a["v"] != b["u"] for a, b in zip(path, path[1:]))):
        return None, []
    if any(metric_text(e.get(field)) == "NOT AVAILABLE" for e in path
           for field in ("distance_m", "travel_time_sec", "hazard_value", "exposure_haz_m")):
        return None, []
    totals = {"distance_m": sum(e["distance_m"] for e in path),
              "travel_time_sec": sum(e["travel_time_sec"] for e in path),
              "exposure_haz_m": sum(e["distance_m"] * e["hazard_value"] for e in path),
              "max_edge_hazard": max(e["hazard_value"] for e in path)}
    if (any(metric_text(route.get(field)) == "NOT AVAILABLE"
            or not isclose(route[field], value, rel_tol=1e-12, abs_tol=1e-9) for field, value in totals.items())
            or any(not isclose(e["exposure_haz_m"], e["distance_m"] * e["hazard_value"],
                               rel_tol=1e-12, abs_tol=1e-9) for e in path)
            or metric_text(route.get("score")) == "NOT AVAILABLE"):
        return None, []
    return route, path


def recommendation_text(result):
    route, path = recommended_path(result)
    if route is None:
        return ("No displayable independently verified recommendation. "
                "No green route is asserted. See Research checks for the outcome; "
                "missing or inconsistent metrics are not replaced with zero.")
    text = [f"Independently verified recommendation: {route['route_id']}. "
            f"{metric_text(route['travel_time_sec'])} s, {metric_text(route['distance_m'])} m; "
            f"exposure {metric_text(route['exposure_haz_m'])} hazard-metres; "
            f"maximum edge hazard {metric_text(route['max_edge_hazard'])}."]
    others = [r for r in result.assessment["routes"] if r["route_id"] != route["route_id"]
              and r.get("eligibility") == "eligible"
              and all(metric_text(r.get(k)) != "NOT AVAILABLE" for k in ("travel_time_sec", "exposure_haz_m", "score"))]
    if others:
        other = min(others, key=lambda r: (r["score"], r["route_id"]))
        time_delta = route["travel_time_sec"] - other["travel_time_sec"]
        exposure_delta = route["exposure_haz_m"] - other["exposure_haz_m"]
        text.append(f"Compared with {other['route_id']}: "
                    f"{metric_text(abs(time_delta))} s {'faster' if time_delta < 0 else 'slower' if time_delta > 0 else 'difference in travel time'}; "
                    f"{metric_text(abs(exposure_delta))} hazard-metres "
                    f"{'more' if exposure_delta > 0 else 'less' if exposure_delta < 0 else 'difference in'} exposure.")
    ties = [r["route_id"] for r in others if r["score"] == route["score"]]
    if ties:
        text.append(f"Exact score tie: {', '.join([route['route_id']] + ties)} = {metric_text(route['score'])}. "
                    "The existing rule chooses the smaller route ID (Unicode order) for an exact tie.")
    else:
        text.append("No exact score tie with another eligible route; the lowest weighted score ranks first.")
    text.append(f"Time/exposure weights: {metric_text(result.mission['time_weight'])}/"
                f"{metric_text(result.mission['exposure_weight'])}, after eligible-set min-max normalization. "
                "Scores are not hazard probabilities.")
    text.append("Keyed path: " + "; ".join(f"{e['u']} -> {e['v']} [{e['key']}]" for e in path) + ".")
    text.append("Travel: " + " + ".join(metric_text(e["travel_time_sec"]) for e in path)
                + f" = {metric_text(route['travel_time_sec'])} s | Distance: "
                + " + ".join(metric_text(e["distance_m"]) for e in path)
                + f" = {metric_text(route['distance_m'])} m.")
    text.append("Exposure: " + " + ".join(f"({metric_text(e['distance_m'])} x {metric_text(e['hazard_value'])})" for e in path)
                + f" = {metric_text(route['exposure_haz_m'])} hazard-metres | "
                + "Max hazard: max(" + ", ".join(metric_text(e["hazard_value"]) for e in path)
                + f") = {metric_text(route['max_edge_hazard'])}.")
    return "\n".join(text)


def topology_positions(graph, width, height, origin):
    """Display-only layers from actual connectivity; never geographical positions."""
    levels = {origin: 0}
    pending = [origin]
    while pending:
        node = pending.pop(0)
        for edge in graph["edges"]:
            if edge["u"] == node and edge["v"] not in levels:
                levels[edge["v"]] = levels[node] + 1
                pending.append(edge["v"])
    last = max(levels.values(), default=0)
    groups = {}
    for node in graph["nodes"]:
        groups.setdefault(levels.get(node, last+1), []).append(node)
    maximum = max(groups, default=0)
    positions = {}
    for level, nodes in groups.items():
        for index, node in enumerate(sorted(nodes)):
            positions[node] = (100 + (width-200)*level/max(1, maximum),
                               62 + (height-106)*index/(len(nodes)-1) if len(nodes) > 1 else (height+18)/2)
    return positions


class EvaluationApp:
    def __init__(self, root):
        self.root = root
        self.events, self.running, self.result, self.history = Queue(), False, None, []
        root.title("Component 4 | Synthetic Research Evaluation")
        root.geometry("1150x790")
        root.minsize(900, 650)
        root.protocol("WM_DELETE_WINDOW", self.close)
        style = ttk.Style(root)
        style.configure("Title.TLabel", font=("Segoe UI", 16, "bold"))
        style.configure("Note.TLabel", foreground="#7b3300", font=("Segoe UI", 10, "bold"))
        frame = ttk.Frame(root, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Agentic AI orchestration & decision support", style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, text="SYNTHETIC DATA ONLY | No real incidents, roads or field dispatch", style="Note.TLabel").pack(anchor="w", pady=(3, 9))
        controls = ttk.Frame(frame)
        controls.pack(fill="x")
        self.scenario = tk.StringVar(value=SCENARIOS["normal"][0])
        self.mode = tk.StringVar(value="Offline | RulePlanner")
        self.selector = ttk.Combobox(controls, textvariable=self.scenario, state="readonly", width=47,
                                     values=[value[0] for value in SCENARIOS.values()])
        self.selector.pack(side="left")
        self.mode_selector = ttk.Combobox(controls, textvariable=self.mode, state="readonly", width=30,
                    values=("Offline | RulePlanner", "Live Groq | openai/gpt-oss-20b"))
        self.mode_selector.pack(side="left", padx=8)
        self.run_button = ttk.Button(controls, text="Run scenario", command=self.start_run)
        self.run_button.pack(side="left")
        self.export_button = ttk.Button(controls, text="Export JSON", command=self.export, state="disabled")
        self.export_button.pack(side="left", padx=8)
        self.status = tk.StringVar(value="Ready. Offline mode needs no credentials or network.")
        ttk.Label(frame, textvariable=self.status, wraplength=1080).pack(anchor="w", pady=(9, 5))
        self.evidence = tk.StringVar(value="No run yet. No historical experiment has been preloaded.")
        ttk.Label(frame, textvariable=self.evidence, wraplength=1080).pack(anchor="w", pady=(0, 8))
        notebook = ttk.Notebook(frame)
        notebook.pack(fill="both", expand=True)
        execution, routes, evaluation, comparison, limits = [ttk.Frame(notebook, padding=8) for _ in range(5)]
        for tab, label in zip((execution, routes, evaluation, comparison, limits),
                              ("Execution", "Routes & topology", "Research checks", "Paired comparison", "Scope & limitations")):
            notebook.add(tab, text=label)
        self.decision_table = self.table(execution,
            ("decision", "action", "tool", "host accepts", "executed", "verified", "result"), height=7)
        self.decision_table.bind("<<TreeviewSelect>>", self.select_decision)
        self.detail = self.text(execution, height=9)
        self.live_log = self.text(execution, height=5)
        self.route_table = self.table(routes,
            ("status", "route", "distance (m)", "time (s)", "exposure (haz*m)", "max hazard", "eligibility", "score (lower wins)"), height=4)
        for column, width in zip(self.route_table["columns"], (145, 65, 95, 80, 125, 90, 105, 190)):
            self.route_table.column(column, width=width)
        self.route_table.tag_configure("recommended", background="#e2f4ea", font=("Segoe UI", 10, "bold"))
        self.route_explanation = tk.StringVar(value="No authoritative alternatives yet.")
        explanation = ttk.Label(routes, textvariable=self.route_explanation, wraplength=1040)
        explanation.pack(fill="x", pady=6)
        explanation.bind("<Configure>", lambda event: explanation.configure(wraplength=max(250, event.width-8)))
        graph_frame = ttk.Frame(routes)
        graph_frame.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(graph_frame, height=260, background="#f6f8fa", highlightthickness=0)
        graph_scroll = ttk.Scrollbar(graph_frame, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=graph_scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        graph_scroll.pack(side="right", fill="y")
        self.canvas.bind("<Configure>", lambda _: self.draw_graph())
        self.checks = self.table(evaluation, ("independent check", "status"), height=5)
        self.metrics_text = self.text(evaluation, height=16)
        self.compare_text = self.text(comparison, height=24)
        self.replace_text(self.compare_text,
            "NOT TESTED: no actual paired offline and live runs exist in this application session.\n"
            "Run the same scenario once in each mode to obtain a descriptive comparison.\n"
            "No live trials run automatically. Tokens measure usage, not accuracy.")
        notes = self.text(limits, height=24)
        self.replace_text(notes, "IMPLEMENTED\nReal AdaptiveController, RulePlanner or AdaptiveLLMPlanner, "
            "SyntheticTools, separate SyntheticAuthority, host authorization and strict decoding.\n"
            "Independent exhaustive path/metric and final-state evaluation.\n\nLIMITATIONS\n" +
            "\n".join("- " + item for item in LIMITATIONS) +
            "\n\nPASS means this particular scenario's assertions passed. NOT TESTED means no evidence "
            "for that check. This application does not reproduce ReAct, ToolSandbox, tau-bench or AgentDojo. "
            "It adapts their evaluation principles; see the README for sources.\n\n"
            "No live LLM results are bundled. Groq errors are reported honestly without fallback. "
            "The graph drawing shows actual keyed connectivity with arbitrary display positions; it is not a map.\n"
            "No claim that the project is 70% complete follows from a demonstration.")
        self._poll_token = root.after(60, self.poll)

    @staticmethod
    def table(parent, columns, *, height):
        holder = ttk.Frame(parent)
        holder.pack(fill="x", pady=3)
        table = ttk.Treeview(holder, columns=columns, show="headings", height=height)
        for column in columns:
            table.heading(column, text=column)
            table.column(column, width=140 if len(columns) > 2 else 430, minwidth=60)
        scrollbar = ttk.Scrollbar(holder, orient="horizontal", command=table.xview)
        table.configure(xscrollcommand=scrollbar.set)
        table.pack(fill="x")
        scrollbar.pack(fill="x")
        return table

    @staticmethod
    def text(parent, *, height):
        holder = ttk.Frame(parent)
        holder.pack(fill="both", expand=True, pady=4)
        text = tk.Text(holder, height=height, wrap="word", font=("Segoe UI", 10), state="disabled")
        scrollbar = ttk.Scrollbar(holder, command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        return text

    @staticmethod
    def replace_text(widget, value):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", value)
        widget.configure(state="disabled")

    def append_log(self, value):
        self.live_log.configure(state="normal")
        self.live_log.insert("end", value + "\n")
        self.live_log.see("end")
        self.live_log.configure(state="disabled")

    def set_running(self, running):
        self.running = running
        self.run_button.configure(state="disabled" if running else "normal")
        self.selector.configure(state="disabled" if running else "readonly")
        self.mode_selector.configure(state="disabled" if running else "readonly")
        self.export_button.configure(state="disabled" if running or self.result is None else "normal")

    def start_run(self):
        if self.running:
            return
        scenario = next(key for key, value in SCENARIOS.items() if value[0] == self.scenario.get())
        mode = "rule" if self.mode.get().startswith("Offline") else "groq"
        if mode == "groq" and not messagebox.askyesno("Authorize one live synthetic mission",
                "Send bounded synthetic observations to Groq using openai/gpt-oss-20b?\n\n"
                "Maximum 5 model requests, 4 tool attempts, 120 s cooperative budget.\n"
                "No retries or fallback. A new run may consume account quota.\n"
                "No real incident data is sent. This approval applies to this run only.", parent=self.root):
            return
        self.result = None
        self.set_running(True)
        for table in (self.decision_table, self.route_table, self.checks):
            table.delete(*table.get_children())
        for widget in (self.detail, self.live_log, self.metrics_text):
            self.replace_text(widget, "")
        self.replace_text(self.compare_text, "NOT TESTED for this run: waiting for an actual result.")
        self.canvas.delete("all")
        self.canvas.yview_moveto(0)
        self.route_explanation.set("No new authoritative result yet.")
        self.status.set(("OFFLINE RULE" if mode == "rule" else "LIVE GROQ") + " | Running genuine controller; no replay.")
        self.evidence.set("Waiting for the first real host observation.")

        def work():
            try:
                result = run_evaluation(scenario, mode=mode, authorize_live=(mode == "groq"),
                                        observer=self.events.put)
                self.events.put(("result", result))
            except Exception:
                self.events.put(("error", "Evaluation unavailable; no result or recommendation asserted."))
        Thread(target=work, name="c4-evaluation", daemon=False).start()

    def poll(self):
        try:
            while True:
                event = self.events.get_nowait()
                if type(event) is tuple:
                    if event[0] == "result":
                        self.show_result(event[1])
                    else:
                        self.set_running(False)
                        self.status.set(event[1])
                elif event["event"] == "observation":
                    self.show_evidence(event["evidence"])
                    self.append_log(f"Decision {event['number']}: observed eligible tools: " +
                                    (", ".join(event["eligible"]) or "none"))
                    if event["previous_tool"]:
                        self.append_log(f"Prior tool {event['previous_tool']}: verified={event['previous_verified']}; "
                                        f"failure={readable(event['previous_failure'])}")
                elif event["event"] == "proposal":
                    self.append_log(f"Decision {event['number']}: proposal {event['action']} "
                                    f"{event['tool'] or ''} (host validation pending)")
        except Empty:
            pass
        self._poll_token = self.root.after(60, self.poll)

    def show_evidence(self, evidence):
        self.evidence.set(" | ".join(f"{name}: {evidence[name]['availability']}, "
            f"{evidence[name]['freshness']}, {evidence[name]['verification']}" for name in ("hazard", "graph")) +
            f" | alternatives: {evidence['alternatives']}")

    def show_result(self, result):
        self.result = result
        self.history.append(result)
        self.set_running(False)
        self.show_evidence(result.final_evidence)
        self.status.set(f"{result.source.upper()} | {result.assessment['verdict']} | "
                        f"{result.assessment['classification']} | controller: {result.actual_outcome}")
        for decision in result.decisions:
            self.decision_table.insert("", "end", iid=str(decision["number"]), values=(
                decision["number"], readable(decision["action"]), readable(decision["tool"]),
                decision["host_accepted"], readable(decision["executed"]), readable(decision["verified"]),
                decision["failure"] or decision["reason"]))
        recommended, _ = recommended_path(result)
        for route in result.assessment["routes"]:
            selected = recommended is not None and route["route_id"] == recommended["route_id"]
            self.route_table.insert("", "end", values=("RECOMMENDED" if selected else "Alternative", route["route_id"],
                *(metric_text(route.get(key)) for key in ("distance_m", "travel_time_sec", "exposure_haz_m", "max_edge_hazard")),
                route["eligibility"], metric_text(route.get("score"))), tags=("recommended",) if selected else ())
        self.route_explanation.set(recommendation_text(result))
        for check in result.assessment["checks"]:
            self.checks.insert("", "end", values=(check["name"], check["status"]))
        lines = ["Expected: " + result.expected_outcome, "Actual: " + result.actual_outcome,
            f"Task completion: {result.assessment['task_completed_correctly']} | "
            f"Correct safe termination: {result.assessment['safe_termination_correct']}",
            "Run: " + result.run_id, "Source: " + result.source, "Planner: " + result.planner_id]
        lines.extend(f"{key.replace('_', ' ')}: {readable(value)}" for key, value in result.metrics.items())
        lines.append("Model diagnostics: " + (json.dumps(result.model_diagnostics) if result.model_diagnostics else "NOT TESTED (offline)"))
        self.replace_text(self.metrics_text, "\n".join(lines))
        counterpart = next((r for r in reversed(self.history[:-1]) if r.comparison_key == result.comparison_key
                            and r.source != result.source), None)
        comparison = compare_runs(result, counterpart)
        self.replace_text(self.compare_text, json.dumps(comparison, indent=2))
        self.draw_graph()
        if result.decisions:
            self.decision_table.selection_set("1")
            self.select_decision()

    def select_decision(self, _=None):
        selected = self.decision_table.selection()
        if selected and self.result:
            decision = self.result.decisions[int(selected[0])-1]
            self.replace_text(self.detail, json.dumps(decision, indent=2))

    def draw_graph(self):
        self.canvas.delete("all")
        self.canvas.create_text(12, 12, anchor="nw", text="SYNTHETIC TOPOLOGY | arbitrary display layout, not geographic geometry")
        if self.result is None or not self.result.graph["nodes"]:
            self.canvas.create_text(20, 55, anchor="nw", text="No active verified graph to display.")
            return
        width, height = max(self.canvas.winfo_width(), 700), max(self.canvas.winfo_height(), 260)
        self.canvas.configure(scrollregion=(0, 0, width, height))
        nodes = self.result.graph["nodes"]
        positions = topology_positions(self.result.graph, width, height, self.result.mission["origin"])
        chosen, path = recommended_path(self.result)
        highlight = {(e["u"], e["v"], e["key"]) for e in path} if chosen else set()
        groups = {}
        for edge in self.result.graph["edges"]:
            groups.setdefault((edge["u"], edge["v"]), []).append(edge["key"])
        labels = []
        for index, edge in enumerate(self.result.graph["edges"]):
            u, v, key = edge["u"], edge["v"], edge["key"]
            x1, y1 = positions[u]
            x2, y2 = positions[v]
            length = hypot(x2-x1, y2-y1)
            ux, uy = (x2-x1)/length, (y2-y1)/length
            keys = sorted(groups[(u, v)])
            lane = (keys.index(key)-(len(keys)-1)/2)*100
            mid_x, mid_y = (x1+x2)/2 - uy*lane, (y1+y2)/2 + ux*lane
            color = "#087f5b" if (u, v, key) in highlight else "#8c969f"
            tag = f"edge-{index}"
            self.canvas.create_line(x1+ux*18, y1+uy*18, mid_x, mid_y, x2-ux*18, y2-uy*18, smooth=True,
                arrow="last", width=4 if (u, v, key) in highlight else 2, fill=color,
                tags=("topology_edge", tag) + (("recommended_edge",) if (u, v, key) in highlight else ()))
            labels.append(((x1+x2)/2 - uy*lane/2, (y1+y2)/2 + ux*lane/2,
                           f"{u} -> {v} [{key}]\n{metric_text(edge.get('travel_time_sec'))} s | "
                           f"{metric_text(edge.get('distance_m'))} m", color, tag))
        for node, (x, y) in positions.items():
            self.canvas.create_oval(x-17, y-17, x+17, y+17, fill="#e6eef5", outline="#3b556f")
            self.canvas.create_text(x, y, text=node, font=("Segoe UI", 11, "bold"))
        occupied = [(x-21, y-21, x+21, y+21) for x, y in positions.values()]
        for x, y, text, color, tag in labels:
            label = self.canvas.create_text(x, y, text=text, fill=color, font=("Segoe UI", 9),
                                             tags=("edge_label", tag))
            # Prefer the path midpoint; move only labels, not road identities,
            # when their actual Tk text boxes would overlap a label or node.
            for dx, dy in sorted(((a, b) for a in (0, -35, 35, -70, 70) for b in (0, -25, 25, -50, 50)),
                                 key=lambda shift: shift[0]**2 + shift[1]**2):
                self.canvas.coords(label, x+dx, y+dy)
                box = self.canvas.bbox(label)
                if (box[0] >= 8 and box[2] <= width-8 and box[1] >= 35 and box[3] <= height-25
                        and not any(box[0] < b[2]+4 and box[2] > b[0]-4
                                    and box[1] < b[3]+4 and box[3] > b[1]-4 for b in occupied)):
                    break
            occupied.append(box)
            backing = self.canvas.create_rectangle(box[0]-3, box[1]-2, box[2]+3, box[3]+2,
                fill="#f6f8fa", outline=color, tags=("edge_label_background", tag))
            self.canvas.tag_lower(backing, label)
        self.canvas.create_text(12, height-12, anchor="w", text="Green: independently verified recommended keyed route | Grey: other edges", font=("Segoe UI", 9))

    def export(self):
        if self.result is None or self.running:
            return
        path = filedialog.asksaveasfilename(parent=self.root, initialdir="D:\\", defaultextension=".json",
            initialfile=self.result.run_id + ".json", filetypes=(("Evaluation JSON", "*.json"),))
        if path:
            try:
                export_result(self.result, path)
                self.status.set("Evaluation JSON exported to the selected D: location.")
            except EvaluationError:
                messagebox.showerror("Export unavailable", "Choose an existing folder on D: outside the repository.", parent=self.root)

    def close(self):
        if self.running:
            messagebox.showinfo("Mission running", "Wait for the bounded run to stop. Synchronous API calls cannot be forcibly interrupted.", parent=self.root)
        else:
            self.root.after_cancel(self._poll_token)
            self.root.destroy()


def main():
    root = tk.Tk()
    EvaluationApp(root)
    root.mainloop()
