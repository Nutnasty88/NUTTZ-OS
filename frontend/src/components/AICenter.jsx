import { useEffect, useState } from "react";
import { getModels } from "../services/api";

export default function AICenter() {
  const [models, setModels] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function loadModels() {
      try {
        const data = await getModels();

        if (Array.isArray(data)) {
          setModels(data);
        } else if (Array.isArray(data.models)) {
          setModels(data.models);
        }

      } catch (err) {
        console.error(err);
      } finally {
        setLoading(false);
      }
    }

    loadModels();
  }, []);

  return (
    <div className="card">
      <h2>🤖 AI Center</h2>

      {loading ? (
        <p>Loading models...</p>
      ) : (
        <>
          <p>Installed Models</p>

          <select style={{ width: "100%", marginTop: 10, padding: 8 }}>
            {models.map((model, index) => (
              <option
                key={index}
                value={model.name || model}
              >
                {model.name || model}
              </option>
            ))}
          </select>
        </>
      )}
    </div>
  );
}