import { zodResolver } from "@hookform/resolvers/zod";
import { useRouter } from "next/navigation";
import { useMemo } from "react";
import { useForm } from "react-hook-form";
import BannerErrorMessage from "./BannerErrorMessage";
import LoadingMessage from "./LoadingMessage";
import { VariableInfoLabel } from "./VariableInfoLabel";
import { useHomeOptions } from "../hooks/use-home-options";
import { getExtendedSearchParameterDefinition } from "../utils/extended-search-parameters";
import {
  buildExtendedSearchZodSchema,
  type ExtendedSearchMetricOption,
} from "../utils/extended-search-schema";
import { getJobMetricShortLabel } from "../utils/jobMetricDisplayLabels";

const EXTENDED_SEARCH_ERROR_SUMMARY_ID = "extended-search-submit-errors";

type SearchFieldLabelProps = {
  parameterName: string;
  className?: string;
};

type ExtendedSearchFormInput = Record<string, string | undefined>;

function isMetricOption(value: unknown): value is ExtendedSearchMetricOption {
  return (
    value !== null &&
    typeof value === "object" &&
    typeof (value as { metric?: unknown }).metric === "string"
  );
}

function normalizeMetricOptions(value: unknown): ExtendedSearchMetricOption[] {
  if (!Array.isArray(value)) return [];
  return value.filter(isMetricOption);
}

function normalizeStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

function SearchFieldLabel({ parameterName, className = "form-label d-block" }: SearchFieldLabelProps) {
  const definition = getExtendedSearchParameterDefinition(parameterName);
  if (!definition) return null;
  return (
    <span className={className} id={`${definition.htmlId}-label`}>
      <VariableInfoLabel
        variableName={definition.metadataKey}
        labelText={definition.label}
        enableHelp
      />
    </span>
  );
}

function ariaLabelledBy(htmlId: string): { "aria-labelledby": string } {
  return { "aria-labelledby": `${htmlId}-label` };
}

type ExtendedSearchProps = { onClose?: () => void };

export default function ExtendedSearch({ onClose }: ExtendedSearchProps) {
  const router = useRouter();
  const { options, error, loading } = useHomeOptions();
  const metrics = useMemo(
    () => normalizeMetricOptions(options?.metrics),
    [options?.metrics],
  );
  const schema = useMemo(() => buildExtendedSearchZodSchema(metrics), [metrics]);
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<ExtendedSearchFormInput>({
    resolver: zodResolver(schema),
    defaultValues: {},
  });

  const invalidFieldIds = useMemo(() => {
    const ids = new Set<string>();
    for (const key of Object.keys(errors)) {
      if (key !== "root") ids.add(key);
    }
    return ids;
  }, [errors]);

  const submitErrors = useMemo(() => {
    const messages = new Set<string>();
    const rootMsg = errors.root?.message;
    if (rootMsg) messages.add(String(rootMsg));
    for (const err of Object.values(errors)) {
      if (err && typeof err === "object" && "message" in err && err.message) {
        messages.add(String(err.message));
      }
    }
    return [...messages];
  }, [errors]);

  const header =
    onClose ? (
      <div className="extended-search-header">
        <span
          className="extended-search-title"
          id="extended-search-dialog-title"
          tabIndex={-1}
        >
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

  const onValidSubmit = (raw: ExtendedSearchFormInput) => {
    const params = Object.fromEntries(
      Object.entries(raw).filter(([, v]) => v != null && String(v).trim() !== ""),
    ) as Record<string, string>;

    if (params.jid) {
      router.push(`/machine/job/${params.jid}/`);
      onClose?.();
      return;
    }
    if (params.host && params.end_time__gte) {
      const qs = new URLSearchParams({
        end_time__gte: params.end_time__gte,
        end_time__lte: params.end_time__lte || "now()",
      }).toString();
      router.push(`/machine/host/${encodeURIComponent(params.host)}/plot/?${qs}`);
      onClose?.();
      return;
    }
    const qs = new URLSearchParams(params).toString();
    router.push(`/machine/jobs/?${qs}`);
    onClose?.();
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

  const queues = normalizeStringArray(options?.queues);
  const states = normalizeStringArray(options?.states);

  function ariaErrorProps(htmlId: string) {
    if (!invalidFieldIds.has(htmlId)) return {};
    return {
      "aria-invalid": true,
      "aria-describedby": `${EXTENDED_SEARCH_ERROR_SUMMARY_ID} ${htmlId}-feedback`,
    };
  }

  function fieldClass(htmlId: string, base = "form-control form-control-sm") {
    return invalidFieldIds.has(htmlId) ? `${base} is-invalid` : base;
  }

  function fieldFeedback(htmlId: string) {
    if (!invalidFieldIds.has(htmlId)) return null;
    return (
      <div id={`${htmlId}-feedback`} className="invalid-feedback d-block">
        Check this value.
      </div>
    );
  }

  function handleClearForm() {
    reset({});
  }

  return (
    <div className="extended-search-panel">
      {header}
      <form id="extended-search-form" onSubmit={handleSubmit(onValidSubmit)} noValidate>
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
        <p className="text-muted small mb-1">
          Search fields are combined (AND). Job ID opens one job (same as Find Job in the header).
        </p>
        <div className="row mb-2">
          <div className="col-12 col-md-2">
            <SearchFieldLabel parameterName="jid" />
          </div>
          <div className="col-12 col-md-4">
            <input
              id="ext-jid"
              type="text"
              className={fieldClass("ext-jid")}
              placeholder="Same as Find Job in header"
              autoComplete="off"
              {...register("jid")}
              {...ariaLabelledBy("ext-jid")}
              {...ariaErrorProps("ext-jid")}
            />
            {fieldFeedback("ext-jid")}
          </div>
        </div>
        <fieldset className="border-0 p-0 mb-3">
          <legend className="h6">Job end time</legend>
          <p className="text-muted small">
            Filters use when the job finished (end time), not when it started.
          </p>
          <div className="row">
            <div className="col-12 col-md-2">
              <SearchFieldLabel parameterName="end_time__gte" />
            </div>
            <div className="col-12 col-md-2">
              <input
                id="ext-end-time-gte"
                type="date"
                className={fieldClass("ext-end-time-gte")}
                {...register("end_time__gte")}
                {...ariaLabelledBy("ext-end-time-gte")}
                {...ariaErrorProps("ext-end-time-gte")}
              />
              {fieldFeedback("ext-end-time-gte")}
            </div>
            <div className="col-12 col-md-2">
              <SearchFieldLabel parameterName="end_time__lte" />
            </div>
            <div className="col-12 col-md-2">
              <input
                id="ext-end-time-lte"
                type="date"
                className={fieldClass("ext-end-time-lte")}
                {...register("end_time__lte")}
                {...ariaLabelledBy("ext-end-time-lte")}
                {...ariaErrorProps("ext-end-time-lte")}
              />
              {fieldFeedback("ext-end-time-lte")}
            </div>
          </div>
        </fieldset>
        <div className="row">
          <div className="col-12 col-md-2">
            <SearchFieldLabel parameterName="host" />
          </div>
          <div className="col-12 col-md-6">
            <input
              type="text"
              className={fieldClass("ext-host")}
              id="ext-host"
              {...register("host")}
              {...ariaLabelledBy("ext-host")}
            />
            <p className="text-muted small mb-0 mt-1">
              Host plus earliest job end date opens the host time-series plot, not the job list.
            </p>
          </div>
        </div>
        <div className="row">
          <div className="col-12 col-md-2">
            <SearchFieldLabel parameterName="username" />
          </div>
          <div className="col-12 col-md-2">
            <input
              type="text"
              className="form-control form-control-sm"
              id="ext-username"
              {...register("username")}
              {...ariaLabelledBy("ext-username")}
            />
          </div>
        </div>
        <div className="row">
          <div className="col-12 col-md-2">
            <SearchFieldLabel parameterName="account__icontains" />
          </div>
          <div className="col-12 col-md-2">
            <input
              type="text"
              className="form-control form-control-sm"
              id="ext-account"
              {...register("account__icontains")}
              {...ariaLabelledBy("ext-account")}
            />
          </div>
        </div>
        <div className="row">
          <div className="col-12 col-md-2">
            <SearchFieldLabel parameterName="state" />
          </div>
          <div className="col-12 col-md-2">
            <select
              className="form-select form-select-sm"
              id="ext-state"
              {...register("state")}
              {...ariaLabelledBy("ext-state")}
            >
              <option value="">Any state</option>
              {states.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </div>
        </div>
        <div className="row">
          <div className="col-12 col-md-2">
            <SearchFieldLabel parameterName="queue" />
          </div>
          <div className="col-12 col-md-2">
            <select
              className="form-select form-select-sm"
              id="ext-queue"
              {...register("queue")}
              {...ariaLabelledBy("ext-queue")}
            >
              <option value="">Any queue</option>
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
              <SearchFieldLabel parameterName="runtime__gte" />
            </div>
            <div className="col-12 col-md-2">
              <input
                id="ext-runtime-gte"
                type="text"
                className={fieldClass("ext-runtime-gte")}
                {...register("runtime__gte")}
                placeholder="min seconds"
                {...ariaLabelledBy("ext-runtime-gte")}
                {...ariaErrorProps("ext-runtime-gte")}
              />
              {fieldFeedback("ext-runtime-gte")}
            </div>
            <div className="col-12 col-md-2">
              <SearchFieldLabel parameterName="runtime__lte" />
            </div>
            <div className="col-12 col-md-2">
              <input
                id="ext-runtime-lte"
                type="text"
                className={fieldClass("ext-runtime-lte")}
                {...register("runtime__lte")}
                placeholder="max seconds"
                {...ariaLabelledBy("ext-runtime-lte")}
                {...ariaErrorProps("ext-runtime-lte")}
              />
              {fieldFeedback("ext-runtime-lte")}
            </div>
          </div>
          <div className="row">
            <div className="col-12 col-md-2">
              <SearchFieldLabel parameterName="nhosts__gte" />
            </div>
            <div className="col-12 col-md-2">
              <input
                id="ext-nhosts-gte"
                type="text"
                className={fieldClass("ext-nhosts-gte")}
                {...register("nhosts__gte")}
                placeholder="min nodes"
                {...ariaLabelledBy("ext-nhosts-gte")}
                {...ariaErrorProps("ext-nhosts-gte")}
              />
              {fieldFeedback("ext-nhosts-gte")}
            </div>
            <div className="col-12 col-md-2">
              <SearchFieldLabel parameterName="nhosts__lte" />
            </div>
            <div className="col-12 col-md-2">
              <input
                id="ext-nhosts-lte"
                type="text"
                className={fieldClass("ext-nhosts-lte")}
                {...register("nhosts__lte")}
                placeholder="max nodes"
                {...ariaLabelledBy("ext-nhosts-lte")}
                {...ariaErrorProps("ext-nhosts-lte")}
              />
              {fieldFeedback("ext-nhosts-lte")}
            </div>
          </div>
          <div className="row">
            <div className="col-12 col-md-2">
              <SearchFieldLabel parameterName="node_hrs__gte" />
            </div>
            <div className="col-12 col-md-2">
              <input
                id="ext-node-hrs-gte"
                type="text"
                className={fieldClass("ext-node-hrs-gte")}
                {...register("node_hrs__gte")}
                placeholder="min node-hrs"
                {...ariaLabelledBy("ext-node-hrs-gte")}
                {...ariaErrorProps("ext-node-hrs-gte")}
              />
              {fieldFeedback("ext-node-hrs-gte")}
            </div>
            <div className="col-12 col-md-2">
              <SearchFieldLabel parameterName="node_hrs__lte" />
            </div>
            <div className="col-12 col-md-2">
              <input
                id="ext-node-hrs-lte"
                type="text"
                className={fieldClass("ext-node-hrs-lte")}
                {...register("node_hrs__lte")}
                placeholder="max node-hrs"
                {...ariaLabelledBy("ext-node-hrs-lte")}
                {...ariaErrorProps("ext-node-hrs-lte")}
              />
              {fieldFeedback("ext-node-hrs-lte")}
            </div>
          </div>
        </fieldset>
        <fieldset className="border-0 p-0 mb-3">
          <legend className="h5">Search on Derived Metrics</legend>
          {metrics.map((m: ExtendedSearchMetricOption, idx: number) => (
            <div className="row" key={m.metric}>
              <div className="col-12 col-md-2">
                <span className="form-label d-block" id={`ext-metric-name-${idx}`}>
                  <VariableInfoLabel
                    variableName={m.metric}
                    labelText={getJobMetricShortLabel(m.metric)}
                    enableHelp
                  />{" "}
                  <span className="text-muted small">
                    ({m.metric}, {m.units})
                  </span>
                </span>
              </div>
              <div className="col-12 col-md-2">
                <label
                  id={`ext-metric-${idx}-gte-label`}
                  htmlFor={`ext-metric-${idx}-gte`}
                  className="visually-hidden"
                >
                  {m.metric} minimum ({m.units})
                </label>
                <input
                  id={`ext-metric-${idx}-gte`}
                  type="text"
                  className={fieldClass(`ext-metric-${idx}-gte`)}
                  {...register(`metrics_${m.metric}__gte`)}
                  placeholder={`Min ${m.units}`}
                  aria-labelledby={`ext-metric-name-${idx} ext-metric-${idx}-gte-label`}
                  {...ariaErrorProps(`ext-metric-${idx}-gte`)}
                />
                {fieldFeedback(`ext-metric-${idx}-gte`)}
              </div>
              <div className="col-12 col-md-2">
                <label
                  id={`ext-metric-${idx}-lte-label`}
                  htmlFor={`ext-metric-${idx}-lte`}
                  className="visually-hidden"
                >
                  {m.metric} maximum ({m.units})
                </label>
                <input
                  id={`ext-metric-${idx}-lte`}
                  type="text"
                  className={fieldClass(`ext-metric-${idx}-lte`)}
                  {...register(`metrics_${m.metric}__lte`)}
                  placeholder={`Max ${m.units}`}
                  aria-labelledby={`ext-metric-name-${idx} ext-metric-${idx}-lte-label`}
                  {...ariaErrorProps(`ext-metric-${idx}-lte`)}
                />
                {fieldFeedback(`ext-metric-${idx}-lte`)}
              </div>
            </div>
          ))}
        </fieldset>
        <div className="extended-search-actions">
          <button type="submit" className="btn btn-primary btn-sm">
            Search
          </button>
          <button type="button" className="btn btn-outline-secondary btn-sm" onClick={handleClearForm}>
            Clear all
          </button>
        </div>
      </form>
    </div>
  );
}
