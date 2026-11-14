// The `hyphen` package ships no types.
declare module "hyphen" {
  const createHyphenator: (patterns: unknown, options?: { async?: boolean; html?: boolean; minWordLength?: number;
    hyphenChar?: string; exceptions?: string[] }) => (text: string) => string;
  export default createHyphenator;
}
declare module "hyphen/patterns/*" {
  const patterns: unknown;
  export default patterns;
}
