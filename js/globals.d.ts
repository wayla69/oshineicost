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
declare var allMonths: string[];        // 01-core.js: Object.defineProperty(window, 'allMonths', …)
// 01-core.js: window.uiDialog / uiConfirm / uiPrompt / askWriteToken — the in-page
// dialogs every slice awaits (the app's browser pane has no prompt()/confirm()).
declare function uiDialog(opt: any): Promise<any>;
declare function uiConfirm(message: string, opt?: any): Promise<boolean>;
declare function uiPrompt(message: string, value?: any, opt?: any): Promise<string | null>;
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
