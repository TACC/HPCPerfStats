import { zodResolver } from "@hookform/resolvers/zod";
import { useRouter } from "next/navigation";
import { useMemo, type ReactNode } from "react";
import { Controller, useForm } from "react-hook-form";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
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

function SearchFieldLabel({ parameterName, className }: SearchFieldLabelProps) {
  const definition = getExtendedSearchParameterDefinition(parameterName);
  if (!definition) return null;
  return (
    <span className={cn("block text-sm font-medium", className)} id={`${definition.htmlId}-label`}>
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

type FieldRowProps = {
  label: ReactNode;
  children: ReactNode;
  labelClassName?: string;
  fieldClassName?: string;
};

function FieldRow({
  label,
  children,
  labelClassName = "md:col-span-2",
  fieldClassName = "md:col-span-4",
}: FieldRowProps) {
  return (
    <div className="mb-2 grid grid-cols-1 gap-2 md:grid-cols-12 md:items-start">
      <div className={cn("md:col-span-12", labelClassName)}>{label}</div>
      <div className={cn("md:col-span-12", fieldClassName)}>{children}</div>
    </div>
  );
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
    control,
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
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={onClose}
          aria-label="Close extended search"
        >
          Close
        </Button>
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
          variant="inline"
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
      "aria-invalid": true as const,
      "aria-describedby": `${EXTENDED_SEARCH_ERROR_SUMMARY_ID} ${htmlId}-feedback`,
    };
  }

  function fieldFeedback(htmlId: string) {
    if (!invalidFieldIds.has(htmlId)) return null;
    return (
      <p id={`${htmlId}-feedback`} className="mt-1 text-sm text-destructive">
        Check this value.
      </p>
    );
  }

  function handleClearForm() {
    reset({});
  }

  const compactInputClass = "h-7 text-sm";

  return (
    <div className="extended-search-panel">
      {header}
      <form id="extended-search-form" onSubmit={handleSubmit(onValidSubmit)} noValidate>
        {submitErrors.length > 0 ? (
          <Alert variant="destructive" id={EXTENDED_SEARCH_ERROR_SUMMARY_ID} className="mb-2 py-2">
            <AlertDescription>
              <ul className="mb-0 list-disc pl-5 text-sm">
                {submitErrors.map((msg) => (
                  <li key={msg}>{msg}</li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        ) : null}
        <p className="mb-1 text-sm text-muted-foreground">
          Search fields are combined (AND). Job ID opens one job (same as Find Job in the header).
        </p>
        <FieldRow
          label={<SearchFieldLabel parameterName="jid" />}
          fieldClassName="md:col-span-4"
          labelClassName="md:col-span-2"
        >
          <Input
            id="ext-jid"
            type="text"
            className={compactInputClass}
            placeholder="Same as Find Job in header"
            autoComplete="off"
            {...register("jid")}
            {...ariaLabelledBy("ext-jid")}
            {...ariaErrorProps("ext-jid")}
          />
          {fieldFeedback("ext-jid")}
        </FieldRow>
        <fieldset className="mb-3 border-0 p-0">
          <legend className="text-base font-semibold">Job end time</legend>
          <p className="text-sm text-muted-foreground">
            Filters use when the job finished (end time), not when it started.
          </p>
          <div className="mb-2 grid grid-cols-1 gap-2 md:grid-cols-12 md:items-start">
            <div className="md:col-span-2">
              <SearchFieldLabel parameterName="end_time__gte" />
            </div>
            <div className="md:col-span-2">
              <Input
                id="ext-end-time-gte"
                type="date"
                className={compactInputClass}
                {...register("end_time__gte")}
                {...ariaLabelledBy("ext-end-time-gte")}
                {...ariaErrorProps("ext-end-time-gte")}
              />
              {fieldFeedback("ext-end-time-gte")}
            </div>
            <div className="md:col-span-2">
              <SearchFieldLabel parameterName="end_time__lte" />
            </div>
            <div className="md:col-span-2">
              <Input
                id="ext-end-time-lte"
                type="date"
                className={compactInputClass}
                {...register("end_time__lte")}
                {...ariaLabelledBy("ext-end-time-lte")}
                {...ariaErrorProps("ext-end-time-lte")}
              />
              {fieldFeedback("ext-end-time-lte")}
            </div>
          </div>
        </fieldset>
        <FieldRow label={<SearchFieldLabel parameterName="host" />} fieldClassName="md:col-span-6">
          <Input
            type="text"
            className={compactInputClass}
            id="ext-host"
            {...register("host")}
            {...ariaLabelledBy("ext-host")}
          />
          <p className="mb-0 mt-1 text-sm text-muted-foreground">
            Host plus earliest job end date opens the host time-series plot, not the job list.
          </p>
        </FieldRow>
        <FieldRow label={<SearchFieldLabel parameterName="username" />} fieldClassName="md:col-span-2">
          <Input
            type="text"
            className={compactInputClass}
            id="ext-username"
            {...register("username")}
            {...ariaLabelledBy("ext-username")}
          />
        </FieldRow>
        <FieldRow
          label={<SearchFieldLabel parameterName="account__icontains" />}
          fieldClassName="md:col-span-2"
        >
          <Input
            type="text"
            className={compactInputClass}
            id="ext-account"
            {...register("account__icontains")}
            {...ariaLabelledBy("ext-account")}
          />
        </FieldRow>
        <FieldRow label={<SearchFieldLabel parameterName="state" />} fieldClassName="md:col-span-2">
          <Controller
            name="state"
            control={control}
            render={({ field }) => (
              <Select
                value={field.value && field.value !== "" ? field.value : "__any__"}
                onValueChange={(value) => field.onChange(value === "__any__" ? "" : value)}
              >
                <SelectTrigger
                  id="ext-state"
                  size="sm"
                  className="w-full"
                  {...ariaLabelledBy("ext-state")}
                >
                  <SelectValue placeholder="Any state" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__any__">Any state</SelectItem>
                  {states.map((s) => (
                    <SelectItem key={s} value={s}>
                      {s}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          />
        </FieldRow>
        <FieldRow label={<SearchFieldLabel parameterName="queue" />} fieldClassName="md:col-span-2">
          <Controller
            name="queue"
            control={control}
            render={({ field }) => (
              <Select
                value={field.value && field.value !== "" ? field.value : "__any__"}
                onValueChange={(value) => field.onChange(value === "__any__" ? "" : value)}
              >
                <SelectTrigger
                  id="ext-queue"
                  size="sm"
                  className="w-full"
                  {...ariaLabelledBy("ext-queue")}
                >
                  <SelectValue placeholder="Any queue" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__any__">Any queue</SelectItem>
                  {queues.map((q) => (
                    <SelectItem key={q} value={q}>
                      {q}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          />
        </FieldRow>
        <fieldset className="mb-3 border-0 p-0">
          <legend className="text-base font-semibold">Search on Resources</legend>
          <div className="mb-2 grid grid-cols-1 gap-2 md:grid-cols-12 md:items-start">
            <div className="md:col-span-2">
              <SearchFieldLabel parameterName="runtime__gte" />
            </div>
            <div className="md:col-span-2">
              <Input
                id="ext-runtime-gte"
                type="text"
                className={compactInputClass}
                {...register("runtime__gte")}
                placeholder="min seconds"
                {...ariaLabelledBy("ext-runtime-gte")}
                {...ariaErrorProps("ext-runtime-gte")}
              />
              {fieldFeedback("ext-runtime-gte")}
            </div>
            <div className="md:col-span-2">
              <SearchFieldLabel parameterName="runtime__lte" />
            </div>
            <div className="md:col-span-2">
              <Input
                id="ext-runtime-lte"
                type="text"
                className={compactInputClass}
                {...register("runtime__lte")}
                placeholder="max seconds"
                {...ariaLabelledBy("ext-runtime-lte")}
                {...ariaErrorProps("ext-runtime-lte")}
              />
              {fieldFeedback("ext-runtime-lte")}
            </div>
          </div>
          <div className="mb-2 grid grid-cols-1 gap-2 md:grid-cols-12 md:items-start">
            <div className="md:col-span-2">
              <SearchFieldLabel parameterName="nhosts__gte" />
            </div>
            <div className="md:col-span-2">
              <Input
                id="ext-nhosts-gte"
                type="text"
                className={compactInputClass}
                {...register("nhosts__gte")}
                placeholder="min nodes"
                {...ariaLabelledBy("ext-nhosts-gte")}
                {...ariaErrorProps("ext-nhosts-gte")}
              />
              {fieldFeedback("ext-nhosts-gte")}
            </div>
            <div className="md:col-span-2">
              <SearchFieldLabel parameterName="nhosts__lte" />
            </div>
            <div className="md:col-span-2">
              <Input
                id="ext-nhosts-lte"
                type="text"
                className={compactInputClass}
                {...register("nhosts__lte")}
                placeholder="max nodes"
                {...ariaLabelledBy("ext-nhosts-lte")}
                {...ariaErrorProps("ext-nhosts-lte")}
              />
              {fieldFeedback("ext-nhosts-lte")}
            </div>
          </div>
          <div className="mb-2 grid grid-cols-1 gap-2 md:grid-cols-12 md:items-start">
            <div className="md:col-span-2">
              <SearchFieldLabel parameterName="node_hrs__gte" />
            </div>
            <div className="md:col-span-2">
              <Input
                id="ext-node-hrs-gte"
                type="text"
                className={compactInputClass}
                {...register("node_hrs__gte")}
                placeholder="min node-hrs"
                {...ariaLabelledBy("ext-node-hrs-gte")}
                {...ariaErrorProps("ext-node-hrs-gte")}
              />
              {fieldFeedback("ext-node-hrs-gte")}
            </div>
            <div className="md:col-span-2">
              <SearchFieldLabel parameterName="node_hrs__lte" />
            </div>
            <div className="md:col-span-2">
              <Input
                id="ext-node-hrs-lte"
                type="text"
                className={compactInputClass}
                {...register("node_hrs__lte")}
                placeholder="max node-hrs"
                {...ariaLabelledBy("ext-node-hrs-lte")}
                {...ariaErrorProps("ext-node-hrs-lte")}
              />
              {fieldFeedback("ext-node-hrs-lte")}
            </div>
          </div>
        </fieldset>
        <fieldset className="mb-3 border-0 p-0">
          <legend className="text-base font-semibold">Search on Derived Metrics</legend>
          {metrics.map((m: ExtendedSearchMetricOption, idx: number) => (
            <div className="mb-2 grid grid-cols-1 gap-2 md:grid-cols-12 md:items-start" key={m.metric}>
              <div className="md:col-span-2">
                <span className="block text-sm font-medium" id={`ext-metric-name-${idx}`}>
                  <VariableInfoLabel
                    variableName={m.metric}
                    labelText={getJobMetricShortLabel(m.metric)}
                    enableHelp
                  />{" "}
                  <span className="text-sm text-muted-foreground">
                    ({m.metric}, {m.units})
                  </span>
                </span>
              </div>
              <div className="md:col-span-2">
                <Label
                  id={`ext-metric-${idx}-gte-label`}
                  htmlFor={`ext-metric-${idx}-gte`}
                  className="sr-only"
                >
                  {m.metric} minimum ({m.units})
                </Label>
                <Input
                  id={`ext-metric-${idx}-gte`}
                  type="text"
                  className={compactInputClass}
                  {...register(`metrics_${m.metric}__gte`)}
                  placeholder={`Min ${m.units}`}
                  aria-labelledby={`ext-metric-name-${idx} ext-metric-${idx}-gte-label`}
                  {...ariaErrorProps(`ext-metric-${idx}-gte`)}
                />
                {fieldFeedback(`ext-metric-${idx}-gte`)}
              </div>
              <div className="md:col-span-2">
                <Label
                  id={`ext-metric-${idx}-lte-label`}
                  htmlFor={`ext-metric-${idx}-lte`}
                  className="sr-only"
                >
                  {m.metric} maximum ({m.units})
                </Label>
                <Input
                  id={`ext-metric-${idx}-lte`}
                  type="text"
                  className={compactInputClass}
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
          <Button type="submit" size="sm">
            Search
          </Button>
          <Button type="button" variant="outline" size="sm" onClick={handleClearForm}>
            Clear all
          </Button>
        </div>
      </form>
    </div>
  );
}
