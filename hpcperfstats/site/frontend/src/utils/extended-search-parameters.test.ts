import { describe, expect, it } from "vitest";
import { PROJECT_FIELD_LABEL } from "./site-field-labels";
import {
  EXTENDED_SEARCH_ALLOWED_PARAM_NAMES,
  EXTENDED_SEARCH_DATE_RANGE_PAIRS,
  EXTENDED_SEARCH_NUMERIC_RANGE_PAIRS,
  EXTENDED_SEARCH_PARAMETER_DEFINITIONS,
  getExtendedSearchParameterDefinition,
} from "./extended-search-parameters";

describe("EXTENDED_SEARCH_PARAMETER_DEFINITIONS", () => {
  it("includes unique parameter names with required metadata fields", () => {
    const names = EXTENDED_SEARCH_PARAMETER_DEFINITIONS.map((param) => param.name);
    expect(new Set(names).size).toBe(names.length);
    for (const param of EXTENDED_SEARCH_PARAMETER_DEFINITIONS) {
      expect(param.htmlId).toEqual(expect.any(String));
      expect(param.label).toEqual(expect.any(String));
      expect(param.metadataKey).toEqual(expect.any(String));
      expect(param.navigation).toMatch(/^(job|jobs)$/);
    }
  });

  it("uses the canonical Project label for account search", () => {
    const account = EXTENDED_SEARCH_PARAMETER_DEFINITIONS.find(
      (param) => param.name === "account__icontains",
    );
    expect(account?.label).toBe(PROJECT_FIELD_LABEL);
  });

  it("keeps the allowlist aligned with definitions", () => {
    expect(EXTENDED_SEARCH_ALLOWED_PARAM_NAMES).toEqual(namesFromDefinitions());
  });
});

describe("extended search range pairs", () => {
  it("lists runtime, node count, and node-hours numeric ranges", () => {
    expect(EXTENDED_SEARCH_NUMERIC_RANGE_PAIRS.map((range) => range.label)).toEqual([
      "Runtime",
      "Node count",
      "Node-hours",
    ]);
  });

  it("lists the end date range pair", () => {
    expect(EXTENDED_SEARCH_DATE_RANGE_PAIRS[0]).toMatchObject({
      label: "End date",
      gteKey: "end_time__gte",
      lteKey: "end_time__lte",
    });
  });
});

describe("getExtendedSearchParameterDefinition", () => {
  it("returns a definition for known keys and undefined otherwise", () => {
    expect(getExtendedSearchParameterDefinition("jid")?.navigation).toBe("job");
    expect(getExtendedSearchParameterDefinition("not-a-param")).toBeUndefined();
  });
});

function namesFromDefinitions() {
  return EXTENDED_SEARCH_PARAMETER_DEFINITIONS.map((param) => param.name);
}
