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

function ComparisonTable({ title, data }) {
  if (!data) return null;

  return (
    <div className="table-card">
      <h2>{title}</h2>

      <div className="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>Model</th>
              <th>ROC-AUC</th>
              <th>F1</th>
              <th>Precision</th>
              <th>Recall</th>
            </tr>
          </thead>

          <tbody>
            {data.results.map((row) => (
              <tr
                key={row.Model}
                className={
                  row.Model === data.selected_model
                    ? "selected-row"
                    : ""
                }
              >
                <td>
                  {row.Model}
                  {row.Model === data.selected_model && (
                    <span className="best-badge">Best</span>
                  )}
                </td>

                <td>{row.ROC_AUC_Mean.toFixed(3)}</td>
                <td>{row.F1_Mean.toFixed(3)}</td>
                <td>{row.Precision_Mean.toFixed(3)}</td>
                <td>{row.Recall_Mean.toFixed(3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function FeatureImportance({ title, data }) {
  if (!data) return null;

  const topFeatures = [...data]
    .sort((a, b) => b.Importance - a.Importance)
    .slice(0, 10);

  return (
    <div className="feature-card">
      <h2>{title}</h2>

      <div className="feature-list">
        {topFeatures.map((item, index) => (
          <div className="feature-row" key={item.Feature}>
            <div className="feature-name">
              <span>{index + 1}</span>
              {item.Feature}
            </div>

            <strong>{item.Importance.toFixed(3)}</strong>
          </div>
        ))}
      </div>
    </div>
  );
}

function App() {
  const [floodData, setFloodData] = useState(null);
  const [landslideData, setLandslideData] = useState(null);

  const [floodFeatures, setFloodFeatures] = useState(null);
  const [landslideFeatures, setLandslideFeatures] = useState(null);

  const [error, setError] = useState("");

  useEffect(() => {
    async function loadData() {
      try {
        const [
          floodResponse,
          landslideResponse,
          floodFeatureResponse,
          landslideFeatureResponse,
        ] = await Promise.all([
          fetch(`${API_BASE}/models/flood`),
          fetch(`${API_BASE}/models/landslide`),
          fetch(`${API_BASE}/features/flood`),
          fetch(`${API_BASE}/features/landslide`),
        ]);

        if (
          !floodResponse.ok ||
          !landslideResponse.ok ||
          !floodFeatureResponse.ok ||
          !landslideFeatureResponse.ok
        ) {
          throw new Error("Failed to load prototype results.");
        }

        const flood = await floodResponse.json();
        const landslide = await landslideResponse.json();

        const floodImportance =
          await floodFeatureResponse.json();

        const landslideImportance =
          await landslideFeatureResponse.json();

        setFloodData(flood);
        setLandslideData(landslide);

        setFloodFeatures(floodImportance);
        setLandslideFeatures(landslideImportance);
      } catch (err) {
        setError(err.message);
      }
    }

    loadData();
  }, []);

  return (
    <div className="app">
      <header>
        <p className="eyebrow">
          J26-DS-301 · Component 1
        </p>

        <h1>
          Multi-Hazard Susceptibility Prototype
        </h1>

        <p className="subtitle">
          Flood and Landslide Susceptibility Modelling
          for Sabaragamuwa Province
        </p>

        <p className="prototype-note">
          DEM-only prototype using synthetic historical
          hazard samples and 5-fold spatial cross-validation.
        </p>
      </header>

      {error && (
        <p className="error">
          {error}
        </p>
      )}

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

      <section className="section">
        <h2 className="section-title">
          Baseline Model Comparison
        </h2>

        <div className="comparison-grid">
          <ComparisonTable
            title="Flood Models"
            data={floodData}
          />

          <ComparisonTable
            title="Landslide Models"
            data={landslideData}
          />
        </div>
      </section>

      <section className="section">
        <h2 className="section-title">
          Feature Importance
        </h2>

        <div className="comparison-grid">
          <FeatureImportance
            title="Flood Feature Importance"
            data={floodFeatures}
          />

          <FeatureImportance
            title="Landslide Feature Importance"
            data={landslideFeatures}
          />
        </div>
      </section>
    </div>
  );
}

export default App;