export function queryCatalog(
  question: string, now: Date, zone: string, load: () => Promise<unknown>
): Promise<null | {
  sections: {title: string; text: string; fact_ids: string[]}[];
  choices?: {id: string; label: string}[];
  reason: string;
}>;
