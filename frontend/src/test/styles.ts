import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

/**
 * Class names defined by the island stylesheets and the app's own stylesheet.
 *
 * The timeline's clip bars were styled with Tailwind utilities in a project
 * with no Tailwind, so `absolute` resolved to nothing, the bars stayed
 * statically positioned and every clip stacked at the left of the track.
 * Nothing failed — the classes were simply inert. These names let a test
 * assert that a rendered class actually exists somewhere.
 */
const STYLESHEETS = [
  "../islands/timeline-scrub/timeline-scrub.css",
  "../islands/drag-combine/drag-combine.css",
  "../../../static/css/nakavid.css",
];

function classNamesIn(css: string): Set<string> {
  const found = new Set<string>();
  for (const match of css.matchAll(/\.(-?[_a-zA-Z][\w-]*)/g)) {
    const name = match[1];
    if (name !== undefined) {
      found.add(name);
    }
  }
  return found;
}

export const definedClassNames: Set<string> = STYLESHEETS.reduce(
  (accumulator, relative) => {
    const path = fileURLToPath(new URL(relative, import.meta.url));
    for (const name of classNamesIn(readFileSync(path, "utf8"))) {
      accumulator.add(name);
    }
    return accumulator;
  },
  new Set<string>(),
);

/** Every class on `root` and its descendants that no stylesheet defines. */
export function undefinedClassNames(root: HTMLElement): string[] {
  const missing = new Set<string>();
  const elements = [root, ...Array.from(root.querySelectorAll<HTMLElement>("*"))];
  for (const element of elements) {
    for (const name of Array.from(element.classList)) {
      if (!definedClassNames.has(name)) {
        missing.add(name);
      }
    }
  }
  return [...missing].sort();
}
