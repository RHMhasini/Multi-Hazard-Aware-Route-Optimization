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
                    <span className="best-badge">
                      Best
                    </span>
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
          <div
            className="feature-row"
            key={item.Feature}
          >
            <div className="feature-name">
              <span>{index + 1}</span>
              {item.Feature}
            </div>

            <strong>
              {item.Importance.toFixed(3)}
            </strong>
          </div>
        ))}
      </div>
    </div>
  );
}

function App() {
  const [floodData, setFloodData] = useState(null);
  const [landslideData, setLandslideData] = useState(null);

  const [floodFeatures, setFloodFeatures] =
    useState(null);

  const [landslideFeatures, setLandslideFeatures] =
    useState(null);

  const [roadSummary, setRoadSummary] =
    useState(null);

  const [error, setError] = useState("");

  useEffect(() => {
    async function loadData() {
      try {
        const [
          floodResponse,
          landslideResponse,
          floodFeatureResponse,
          landslideFeatureResponse,
          roadSummaryResponse,
        ] = await Promise.all([
          fetch(`${API_BASE}/models/flood`),
          fetch(`${API_BASE}/models/landslide`),
          fetch(`${API_BASE}/features/flood`),
          fetch(`${API_BASE}/features/landslide`),
          fetch(`${API_BASE}/roads/summary`),
        ]);

        if (
          !floodResponse.ok ||
          !landslideResponse.ok ||
          !floodFeatureResponse.ok ||
          !landslideFeatureResponse.ok ||
          !roadSummaryResponse.ok
        ) {
          throw new Error(
            "Failed to load prototype results."
          );
        }

        const flood =
          await floodResponse.json();

        const landslide =
          await landslideResponse.json();

        const floodImportance =
          await floodFeatureResponse.json();

        const landslideImportance =
          await landslideFeatureResponse.json();

        const roadIntegrationSummary =
          await roadSummaryResponse.json();

        setFloodData(flood);
        setLandslideData(landslide);

        setFloodFeatures(floodImportance);
        setLandslideFeatures(
          landslideImportance
        );

        setRoadSummary(
          roadIntegrationSummary
        );
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
          Flood and Landslide Susceptibility
          Modelling for Sabaragamuwa Province
        </p>

        <p className="prototype-note">
          DEM-only prototype using synthetic
          historical hazard samples and 5-fold
          spatial cross-validation.
        </p>
      </header>

      {error && (
        <p className="error">
          {error}
        </p>
      )}

      {/* Selected Models */}
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

      {/* Model Comparison */}
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

      {/* Feature Importance */}
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

      {/* Susceptibility Maps */}
      <section className="section">
        <h2 className="section-title">
          Susceptibility Maps
        </h2>

        <div className="map-grid">
          <div className="map-card">
            <h2>
              Flood Susceptibility
            </h2>

            <img
              src="/maps/flood_susceptibility.png"
              alt="Flood susceptibility map"
            />
          </div>

          <div className="map-card">
            <h2>
              Landslide Susceptibility
            </h2>

            <img
              src="/maps/landslide_susceptibility.png"
              alt="Landslide susceptibility map"
            />
          </div>

          <div className="map-card map-card-wide">
            <h2>
              Multi-Hazard Susceptibility
            </h2>

            <p className="map-note">
              Pixel-wise MAX fusion of normalized
              flood and landslide scores.
            </p>

            <img
              src="/maps/multi_hazard_max.png"
              alt="Multi-hazard susceptibility map"
            />
          </div>
        </div>
      </section>

      {/* Road Network Integration */}
      {roadSummary && (
        <section className="road-integration-section">
          <div className="section-heading">
            <div>
              <span className="section-label">
                COMPONENT INTEGRATION
              </span>

              <h2>
                Road Network Hazard Integration
              </h2>

              <p>
                Flood and landslide susceptibility
                scores aggregated to the directed road
                network received from Component 2.
              </p>
            </div>

            <span className="integration-badge">
              {roadSummary.coverage_percent.toFixed(2)}%
              {" "}Scored
            </span>
          </div>

          <div className="road-summary-grid">
            <div className="road-stat-card">
              <span>
                Total Directed Edges
              </span>

              <strong>
                {roadSummary.total_edges.toLocaleString()}
              </strong>
            </div>

            <div className="road-stat-card">
              <span>
                Valid Hazard Scores
              </span>

              <strong>
                {roadSummary.valid_edges.toLocaleString()}
              </strong>
            </div>

            <div className="road-stat-card">
              <span>
                Coverage
              </span>

              <strong>
                {roadSummary.coverage_percent.toFixed(2)}%
              </strong>
            </div>

            <div className="road-stat-card">
              <span>
                NoData Edges
              </span>

              <strong>
                {roadSummary.nodata_edges.toLocaleString()}
              </strong>
            </div>

            <div className="road-stat-card">
              <span>
                Flood Dominant
              </span>

              <strong>
                {roadSummary.flood_dominant.toLocaleString()}
              </strong>
            </div>

            <div className="road-stat-card">
              <span>
                Landslide Dominant
              </span>

              <strong>
                {roadSummary.landslide_dominant.toLocaleString()}
              </strong>
            </div>
          </div>

          <div className="road-score-summary">
            <div>
              <span>
                Mean MAX Score
              </span>

              <strong>
                {roadSummary.mean_final_score.toFixed(4)}
              </strong>
            </div>

            <div>
              <span>
                Median MAX Score
              </span>

              <strong>
                {roadSummary.median_final_score.toFixed(4)}
              </strong>
            </div>

            <div>
              <span>
                Maximum MAX Score
              </span>

              <strong>
                {roadSummary.max_final_score.toFixed(4)}
              </strong>
            </div>
          </div>

          <div className="integration-flow">
            <span>
              Component 2 Road Graph
            </span>

            <span>→</span>

            <span>
              Flood + Landslide Susceptibility
            </span>

            <span>→</span>

            <span>
              Road-Level MAX
            </span>

            <span>→</span>

            <span>
              Component 3
            </span>
          </div>

          <p className="road-note">
            4,038 road edges intersect NoData pixels
            at the susceptibility raster boundary.
            These edges are retained as NoData rather
            than being incorrectly assigned zero
            susceptibility.
          </p>
        </section>
      )}
    </div>
  );
}

export default App;