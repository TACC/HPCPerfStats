import { useState } from "react";
import { useNavigate } from "react-router-dom";
import BannerErrorMessage from "./BannerErrorMessage";
import LoadingMessage from "./LoadingMessage";
import { useHomeOptions } from "../hooks/use-home-options";
import { validateExtendedSearchForm } from "../utils/extended-search-validation";

const EXTENDED_SEARCH_ERROR_SUMMARY_ID = "extended-search-submit-errors";

const ALLOWED_PARAMS = [
  "jid",
  "host",
  "username",
  "account__icontains",
  "state",
  "queue",
  "end_time__gte",
  "end_time__lte",
  "runtime__gte",
  "runtime__lte",
  "nhosts__gte",
  "nhosts__lte",
  "node_hrs__gte",
  "node_hrs__lte",
];

export default function ExtendedSearch({ onClose }) {
  const navigate = useNavigate();
  const { options, error, loading } = useHomeOptions();
  const [submitErrors, setSubmitErrors] = useState([]);
  const [invalidFieldIds, setInvalidFieldIds] = useState(() => new Set());

  const header =
    onClose ? (
      <div className="extended-search-header">
        <span className="extended-search-title" id="extended-search-dialog-title">
          Extended search
        </span>
        <button
          type="button"
          className="btn btn-outline-secondary btn-sm"
          onClick={onClose}
          aria-label="Close extended search"
        >
          Close
        </button>
      </div>
    ) : null;

  const handleSubmit = (e) => {
    e.preventDefault();
    const form = e.target;
    const params = {};
    for (const el of form.elements) {
      if (!el.name || !el.value) continue;
      if (el.name.startsWith("metrics_")) {
        params[el.name] = el.value;
        continue;
      }
      if (ALLOWED_PARAMS.includes(el.name)) params[el.name] = el.value;
    }

    setSubmitErrors([]);
    setInvalidFieldIds(new Set());

    const validation = validateExtendedSearchForm(params, options ?? {});
    if (!validation.ok) {
      setSubmitErrors(validation.messages);
      setInvalidFieldIds(validation.invalidHtmlIds);
      return;
    }

    if (params.jid) {
      navigate(`/job/${params.jid}`);
      return;
    }
    if (params.host && params.end_time__gte) {
      const qs = new URLSearchParams({
        end_time__gte: params.end_time__gte,
        end_time__lte: params.end_time__lte || "now()",
      }).toString();
      navigate(`/host/${encodeURIComponent(params.host)}/plot?${qs}`);
      return;
    }
    const qs = new URLSearchParams(params).toString();
    navigate(`/jobs?${qs}`);
  };

  if (loading) {
    return (
      <div className="extended-search-panel">
        {header}
        <LoadingMessage message="Loading search options…" />
      </div>
    );
  }
  if (error) {
    return (
      <div className="extended-search-panel">
        {header}
        <BannerErrorMessage
          message={error}
          className="text-danger"
          style={{ padding: "0.5rem 0" }}
        />
      </div>
    );
  }

  const { metrics = [], queues = [], states = [] } = options || {};

  function ariaErrorProps(htmlId) {
    if (!invalidFieldIds.has(htmlId)) return {};
    return {
      "aria-invalid": true,
      "aria-describedby": EXTENDED_SEARCH_ERROR_SUMMARY_ID,
    };
  }

  return (
    <div className="extended-search-panel">
      {header}
      <form id="extended-search-form" onSubmit={handleSubmit} noValidate>
        {submitErrors.length > 0 ? (
          <div
            id={EXTENDED_SEARCH_ERROR_SUMMARY_ID}
            className="alert alert-danger py-2 small"
            role="alert"
          >
            <ul className="mb-0 ps-3">
              {submitErrors.map((msg) => (
                <li key={msg}>{msg}</li>
              ))}
            </ul>
          </div>
        ) : null}
        <p className="text-muted small">Search fields are combined.</p>
        <div className="row mb-2">
          <div className="col-12 col-md-2">
            <label htmlFor="ext-jid">Job ID</label>
          </div>
          <div className="col-12 col-md-4">
            <input
              id="ext-jid"
              type="text"
              className="form-control form-control-sm"
              name="jid"
              placeholder="Jump directly to a job"
              autoComplete="off"
            />
          </div>
        </div>
        <fieldset className="border-0 p-0 mb-3">
          <legend className="h6">Time range</legend>
          <div className="row">
            <div className="col-12 col-md-2">
              <label htmlFor="ext-end-time-gte">Start Date</label>
            </div>
            <div className="col-12 col-md-2">
              <input
                id="ext-end-time-gte"
                type="date"
                className="form-control form-control-sm"
                name="end_time__gte"
              />
            </div>
            <div className="col-12 col-md-2">
              <label htmlFor="ext-end-time-lte">End Date</label>
            </div>
            <div className="col-12 col-md-2">
              <input
                id="ext-end-time-lte"
                type="date"
                className="form-control form-control-sm"
                name="end_time__lte"
              />
            </div>
          </div>
        </fieldset>
        <div className="row">
          <div className="col-12 col-md-2">
            <label htmlFor="ext-host">Host</label>
          </div>
          <div className="col-12 col-md-2">
            <input
              type="text"
              className="form-control form-control-sm"
              name="host"
              id="ext-host"
            />
          </div>
        </div>
        <div className="row">
          <div className="col-12 col-md-2">
            <label htmlFor="ext-username">Username</label>
          </div>
          <div className="col-12 col-md-2">
            <input
              type="text"
              className="form-control form-control-sm"
              name="username"
              id="ext-username"
            />
          </div>
        </div>
        <div className="row">
          <div className="col-12 col-md-2">
            <label htmlFor="ext-account">Account</label>
          </div>
          <div className="col-12 col-md-2">
            <input
              type="text"
              className="form-control form-control-sm"
              name="account__icontains"
              id="ext-account"
            />
          </div>
        </div>
        <div className="row">
          <div className="col-12 col-md-2">
            <label htmlFor="ext-state">State</label>
          </div>
          <div className="col-12 col-md-2">
            <select className="form-control" id="ext-state" name="state">
              <option value="">--</option>
              {states.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </div>
        </div>
        <div className="row">
          <div className="col-12 col-md-2">
            <label htmlFor="ext-queue">Queue</label>
          </div>
          <div className="col-12 col-md-2">
            <select className="form-control" id="ext-queue" name="queue">
              <option value="">--</option>
              {queues.map((q) => (
                <option key={q}>{q}</option>
              ))}
            </select>
          </div>
        </div>
        <fieldset className="border-0 p-0 mb-3">
          <legend className="h5">Search on Resources</legend>
          <div className="row">
            <div className="col-12 col-md-2">
              <label htmlFor="ext-runtime-gte">Runtime minimum (seconds)</label>
            </div>
            <div className="col-12 col-md-2">
              <input
                id="ext-runtime-gte"
                type="text"
                className="form-control form-control-sm"
                name="runtime__gte"
                placeholder="min seconds"
                {...ariaErrorProps("ext-runtime-gte")}
              />
            </div>
            <div className="col-12 col-md-2">
              <label htmlFor="ext-runtime-lte">Runtime maximum (seconds)</label>
            </div>
            <div className="col-12 col-md-2">
              <input
                id="ext-runtime-lte"
                type="text"
                className="form-control form-control-sm"
                name="runtime__lte"
                placeholder="max seconds"
                {...ariaErrorProps("ext-runtime-lte")}
              />
            </div>
          </div>
          <div className="row">
            <div className="col-12 col-md-2">
              <label htmlFor="ext-nhosts-gte">Nodes minimum</label>
            </div>
            <div className="col-12 col-md-2">
              <input
                id="ext-nhosts-gte"
                type="text"
                className="form-control form-control-sm"
                name="nhosts__gte"
                placeholder="min nodes"
                {...ariaErrorProps("ext-nhosts-gte")}
              />
            </div>
            <div className="col-12 col-md-2">
              <label htmlFor="ext-nhosts-lte">Nodes maximum</label>
            </div>
            <div className="col-12 col-md-2">
              <input
                id="ext-nhosts-lte"
                type="text"
                className="form-control form-control-sm"
                name="nhosts__lte"
                placeholder="max nodes"
                {...ariaErrorProps("ext-nhosts-lte")}
              />
            </div>
          </div>
          <div className="row">
            <div className="col-12 col-md-2">
              <label htmlFor="ext-node-hrs-gte">Node-hours minimum</label>
            </div>
            <div className="col-12 col-md-2">
              <input
                id="ext-node-hrs-gte"
                type="text"
                className="form-control form-control-sm"
                name="node_hrs__gte"
                placeholder="min node-hrs"
                {...ariaErrorProps("ext-node-hrs-gte")}
              />
            </div>
            <div className="col-12 col-md-2">
              <label htmlFor="ext-node-hrs-lte">Node-hours maximum</label>
            </div>
            <div className="col-12 col-md-2">
              <input
                id="ext-node-hrs-lte"
                type="text"
                className="form-control form-control-sm"
                name="node_hrs__lte"
                placeholder="max node-hrs"
                {...ariaErrorProps("ext-node-hrs-lte")}
              />
            </div>
          </div>
        </fieldset>
        <fieldset className="border-0 p-0 mb-3">
          <legend className="h5">Search on Derived Metrics</legend>
          {metrics.map((m, idx) => (
            <div className="row" key={m.metric}>
              <div className="col-12 col-md-2">
                <span className="form-label d-block" id={`ext-metric-name-${idx}`}>
                  {m.metric}{" "}
                  <span className="text-muted small">({m.units})</span>
                </span>
              </div>
              <div className="col-12 col-md-2">
                <label htmlFor={`ext-metric-${idx}-gte`} className="visually-hidden">
                  {m.metric} minimum ({m.units})
                </label>
                <input
                  id={`ext-metric-${idx}-gte`}
                  type="text"
                  className="form-control form-control-sm"
                  name={`metrics_${m.metric}__gte`}
                  placeholder={`Min ${m.units}`}
                  {...ariaErrorProps(`ext-metric-${idx}-gte`)}
                />
              </div>
              <div className="col-12 col-md-2">
                <label htmlFor={`ext-metric-${idx}-lte`} className="visually-hidden">
                  {m.metric} maximum ({m.units})
                </label>
                <input
                  id={`ext-metric-${idx}-lte`}
                  type="text"
                  className="form-control form-control-sm"
                  name={`metrics_${m.metric}__lte`}
                  placeholder={`Max ${m.units}`}
                  {...ariaErrorProps(`ext-metric-${idx}-lte`)}
                />
              </div>
            </div>
          ))}
        </fieldset>
        <button type="submit" className="btn btn-primary btn-sm">
          Search
        </button>
      </form>
    </div>
  );
}
