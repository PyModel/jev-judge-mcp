import registry from "../../../docs/reference/divergences.json" with { type: "json" };

const programCalls = new Set(registry.divergences.find((entry) => entry.id === "program-response-fields").fixtures);

export function fixtureTags(fixture) {
  const tags = new Set(fixture.divergences);
  if (fixture.calls.some((_, index) => programCalls.has(`${fixture.id}#${index}`))) tags.add("ADR-0062");
  return [...tags];
}
