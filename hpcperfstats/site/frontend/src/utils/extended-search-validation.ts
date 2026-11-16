import {
  buildExtendedSearchZodSchema,
  zodIssuesToValidationResult,
  type ExtendedSearchFormValues,
  type ExtendedSearchMetricOption,
} from "./extended-search-schema";

export function validateExtendedSearchForm(
  params: ExtendedSearchFormValues,
  options: { metrics?: ExtendedSearchMetricOption[] } = {},
) {
  const schema = buildExtendedSearchZodSchema(options.metrics || []);
  const result = schema.safeParse(params);
  if (result.success) {
    return { ok: true as const, invalidHtmlIds: new Set<string>(), messages: [] as string[] };
  }
  return zodIssuesToValidationResult(result.error.issues);
}
