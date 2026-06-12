import { PROJECT_FIELD_LABEL } from "./site-field-labels";

export const EXTENDED_SEARCH_PARAMETER_DEFINITIONS = [
  {
    name: "jid",
    htmlId: "ext-jid",
    label: "Job ID",
    metadataKey: "jid",
    navigation: "job",
  },
  {
    name: "end_time__gte",
    htmlId: "ext-end-time-gte",
    label: "Earliest job end date",
    metadataKey: "end_time",
    navigation: "jobs",
    rangeGroup: "end_time",
    rangeSide: "gte",
    valueType: "date",
  },
  {
    name: "end_time__lte",
    htmlId: "ext-end-time-lte",
    label: "Latest job end date",
    metadataKey: "end_time",
    navigation: "jobs",
    rangeGroup: "end_time",
    rangeSide: "lte",
    valueType: "date",
  },
  {
    name: "host",
    htmlId: "ext-host",
    label: "Host",
    metadataKey: "host",
    navigation: "jobs",
  },
  {
    name: "username",
    htmlId: "ext-username",
    label: "Username",
    metadataKey: "username",
    navigation: "jobs",
  },
  {
    name: "account__icontains",
    htmlId: "ext-account",
    label: PROJECT_FIELD_LABEL,
    metadataKey: "account",
    navigation: "jobs",
  },
  {
    name: "state",
    htmlId: "ext-state",
    label: "State",
    metadataKey: "state",
    navigation: "jobs",
  },
  {
    name: "queue",
    htmlId: "ext-queue",
    label: "Queue",
    metadataKey: "queue",
    navigation: "jobs",
  },
  {
    name: "runtime__gte",
    htmlId: "ext-runtime-gte",
    label: "Runtime minimum (seconds)",
    metadataKey: "runtime",
    navigation: "jobs",
    rangeGroup: "runtime",
    rangeSide: "gte",
    valueType: "number",
  },
  {
    name: "runtime__lte",
    htmlId: "ext-runtime-lte",
    label: "Runtime maximum (seconds)",
    metadataKey: "runtime",
    navigation: "jobs",
    rangeGroup: "runtime",
    rangeSide: "lte",
    valueType: "number",
  },
  {
    name: "nhosts__gte",
    htmlId: "ext-nhosts-gte",
    label: "Nodes minimum",
    metadataKey: "nhosts",
    navigation: "jobs",
    rangeGroup: "nhosts",
    rangeSide: "gte",
    valueType: "number",
  },
  {
    name: "nhosts__lte",
    htmlId: "ext-nhosts-lte",
    label: "Nodes maximum",
    metadataKey: "nhosts",
    navigation: "jobs",
    rangeGroup: "nhosts",
    rangeSide: "lte",
    valueType: "number",
  },
  {
    name: "node_hrs__gte",
    htmlId: "ext-node-hrs-gte",
    label: "Node-hours minimum",
    metadataKey: "node_hrs",
    navigation: "jobs",
    rangeGroup: "node_hrs",
    rangeSide: "gte",
    valueType: "number",
  },
  {
    name: "node_hrs__lte",
    htmlId: "ext-node-hrs-lte",
    label: "Node-hours maximum",
    metadataKey: "node_hrs",
    navigation: "jobs",
    rangeGroup: "node_hrs",
    rangeSide: "lte",
    valueType: "number",
  },
];

export const EXTENDED_SEARCH_ALLOWED_PARAM_NAMES =
  EXTENDED_SEARCH_PARAMETER_DEFINITIONS.map((param) => param.name);

export const EXTENDED_SEARCH_NUMERIC_RANGE_PAIRS = [
  {
    label: "Runtime",
    gteKey: "runtime__gte",
    lteKey: "runtime__lte",
    gteId: "ext-runtime-gte",
    lteId: "ext-runtime-lte",
  },
  {
    label: "Node count",
    gteKey: "nhosts__gte",
    lteKey: "nhosts__lte",
    gteId: "ext-nhosts-gte",
    lteId: "ext-nhosts-lte",
  },
  {
    label: "Node-hours",
    gteKey: "node_hrs__gte",
    lteKey: "node_hrs__lte",
    gteId: "ext-node-hrs-gte",
    lteId: "ext-node-hrs-lte",
  },
];

export const EXTENDED_SEARCH_DATE_RANGE_PAIRS = [
  {
    label: "End date",
    gteKey: "end_time__gte",
    lteKey: "end_time__lte",
    gteId: "ext-end-time-gte",
    lteId: "ext-end-time-lte",
  },
];

export function getExtendedSearchParameterDefinition(name: string) {
  return EXTENDED_SEARCH_PARAMETER_DEFINITIONS.find((param) => param.name === name);
}
