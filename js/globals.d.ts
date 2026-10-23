// Ambient declarations for what the slices use but do not define as plain
// JavaScript names — so `tsc --checkJs` (jsconfig.json, scripts/check-js.sh)
// can tell "defined somewhere else" from "defined nowhere". Kept loose on
// purpose (`any`): the check exists to catch an undefined name, a typo, a call
// with the wrong shape or a name used before its declaration, not to type the
// app. Add here when a NEW library or shell-defined global is introduced.

// ── vendor libraries loaded by the shell's _loadVendor() in index.html ──
declare const Chart: any;
declare const ChartDataLabels: any;
declare const ChartAnnotation: any;
declare const marked: any;

// ── globals the shell / the slices define on `window` rather than as names ──
declare var __vendorErr: string[];      // index.html: vendor scripts that failed to load
declare function _clearErrBanner(): void;   // 09b-cost-pnl.js: window._clearErrBanner (PL3)
declare var allMonths: string[];        // 01-core.js: Object.defineProperty(window, 'allMonths', …)
// 01-core.js: window.uiDialog / uiConfirm / uiPrompt / askWriteToken — the in-page
// dialogs every slice awaits (the app's browser pane has no prompt()/confirm()).
declare function uiDialog(opt: any): Promise<any>;
declare function uiConfirm(message: string, opt?: any): Promise<boolean>;
declare function uiNotice(message: string, opt?: any): Promise<boolean>;
declare function uiPrompt(message: string, value?: any, opt?: any): Promise<string | null>;
// PR-D6 (01-core.js): the one toast, the "?" help popover and the finished-work filter
declare function uiToast(text: string, kind?: string, opt?: any): { el: HTMLElement; close(): void; next(text: string, label?: string, fn?: any): any };
declare function uiHelp(title: string, html: string, opener?: any): () => void;
declare function uiQ(key: string, title: string, html?: string | null, cls?: string): string;
declare function uiPaneHelp(tab: string, opener?: any): any;
declare const uiStateFilter: {
  KEY: string; LABEL: Record<string, string>; DEFAULT: string[][];
  get(keys?: string[]): string; set(f: string): void;
  bar(el: any, o: { counts?: any; f: string; pick: string; opts?: string[][] }): void;
  empty(f: string, pick: string, noun?: string): string;
  split<T>(rows: T[], isDone: (r: T) => boolean): { todo: T[]; done: T[]; all: T[]; counts: { todo: number; done: number; all: number } };
};
declare function askWriteToken(message?: string): Promise<string | null>;

// Slices keep per-feature state on window (window.gpbGranularity, …) and read it
// back as a property — any name is allowed there.
interface Window { [key: string]: any; }

// getElementById() types as HTMLElement / querySelector() as Element; the code
// reads the form-control and canvas members through them without casts.
interface HTMLElement {
  value: any; options: any; disabled: any; checked: any; min: any; max: any;
  selectedIndex: any; selectedOptions: any; href: any; width: any; height: any;
  getContext: any; add: any; _t: any; rows: any; files: any; name: any; type: any;
  remove(index?: number): void;
}
interface Element {
  value: any; options: any; disabled: any; checked: any; style: any; dataset: any;
  innerText: any; offsetWidth: any; offsetHeight: any; offsetTop: any; focus: any; click: any;
  href: any; selectedIndex: any; getContext: any; blur: any;
}
interface EventTarget {
  value: any; checked: any; dataset: any; closest: any; classList: any; tagName: any;
  id: any; contains: any; matches: any;
}
