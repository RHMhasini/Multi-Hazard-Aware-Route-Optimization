import { useEffect, useState } from "react";
import "./App.css";

const API_BASE = "http://127.0.0.1:8000";

function ModelCard({ title, data }) {
  if (!data) return null;

  const selected = data.results.find(
    (row) => row.Model === data.selected_model
  );

  return (
    <div className="model-card">
      <h2>{title}</h2>

      <p className="selected-model">
        Selected Model: <strong>{data.selected_model}</strong>
      </p>

      {selected && (
        <div className="metrics">
          <div>
            <span>ROC-AUC</span>
            <strong>{selected.ROC_AUC_Mean.toFixed(3)}</strong>
          </div>

          <div>
            <span>F1 Score</span>
            <strong>{selected.F1_Mean.toFixed(3)}</strong>
          </div>

          <div>
            <span>Precision</span>
            <strong>{selected.Precision_Mean.toFixed(3)}</strong>
          </div>

          <div>
            <span>Recall</span>
            <strong>{selected.Recall_Mean.toFixed(3)}</strong>
          </div>
        </div>
      )}
    </div>
  );
}

function App() {
  const [floodData, setFloodData] = useState(null);
  const [landslideData, setLandslideData] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    async function loadData() {
      try {
        const [floodResponse, landslideResponse] =
          await Promise.all([
            fetch(`${API_BASE}/models/flood`),
            fetch(`${API_BASE}/models/landslide`),
          ]);

        if (!floodResponse.ok || !landslideResponse.ok) {
          throw new Error("Failed to load model results.");
        }

        const flood = await floodResponse.json();
        const landslide = await landslideResponse.json();

        setFloodData(flood);
        setLandslideData(landslide);
      } catch (err) {
        setError(err.message);
      }
    }

    loadData();
  }, []);

  return (
    <div className="app">
      <header>
        <p className="eyebrow">J26-DS-301 · Component 1</p>

        <h1>Multi-Hazard Susceptibility Prototype</h1>

        <p className="subtitle">
          Flood and Landslide Susceptibility Modelling
          for Sabaragamuwa Province
        </p>
      </header>

      {error && <p className="error">{error}</p>}

      <section className="cards">
        <ModelCard
          title="Flood Susceptibility"
          data={floodData}
        />

        <ModelCard
          title="Landslide Susceptibility"
          data={landslideData}
        />
      </section>
    </div>
  );
}

export default App;