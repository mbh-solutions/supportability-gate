export function mixedStackLabel(flags: readonly boolean[] = []): string {
  if (flags[0]) return "blocked-0";
  if (flags[1]) return "blocked-1";
  if (flags[2]) return "blocked-2";
  if (flags[3]) return "blocked-3";
  if (flags[4]) return "blocked-4";
  if (flags[5]) return "blocked-5";
  if (flags[6]) return "blocked-6";
  if (flags[7]) return "blocked-7";
  if (flags[8]) return "blocked-8";
  if (flags[9]) return "blocked-9";
  if (flags[10]) return "blocked-10";
  return "python+typescript";
}
