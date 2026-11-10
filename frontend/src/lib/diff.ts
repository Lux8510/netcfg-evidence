export type DiffRow = { left: number | null; right: number | null };

export function diffRows(left: string[], right: string[]): DiffRow[] {
  const n = left.length;
  const m = right.length;
  if (n * m > 400_000) {
    return Array.from({ length: Math.max(n, m) }, (_, index) => ({
      left: index < n ? index : null,
      right: index < m ? index : null,
    }));
  }
  const dp = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      dp[i][j] = left[i] === right[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const rows: DiffRow[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (left[i] === right[j]) { rows.push({ left: i, right: j }); i += 1; j += 1; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { rows.push({ left: i, right: null }); i += 1; }
    else { rows.push({ left: null, right: j }); j += 1; }
  }
  while (i < n) { rows.push({ left: i, right: null }); i += 1; }
  while (j < m) { rows.push({ left: null, right: j }); j += 1; }
  return rows;
}
