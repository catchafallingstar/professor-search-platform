import from "@hugeicons/react" { HugeiconsIcon }
import from "@hugeicons/core-free-icons" { UnfoldMoreIcon }
import from ...lib.utils { cn }

glob _nativeSelectExcludeKeys: list[str] = ["className", "size", "children"];

def:pub NativeSelect(props: any) -> JsxElement {
    size = props["size"] or "default";

    restProps = {};
    for key in Object.keys(props) {
        if key not in _nativeSelectExcludeKeys {
            restProps[key] = props[key];
        }
    }

    return
        <div
            className={cn(
                "border-input placeholder:text-muted-foreground selection:bg-primary selection:text-primary-foreground dark:bg-input/30 dark:hover:bg-input/50 focus-visible:border-ring focus-visible:ring-ring/50 aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 aria-invalid:border-destructive dark:aria-invalid:border-destructive/50 h-8 w-full min-w-0 appearance-none rounded-none border bg-transparent py-1 pr-8 pl-2.5 text-xs transition-colors select-none focus-visible:ring-1 aria-invalid:ring-1 data-[size=sm]:h-7 data-[size=sm]:rounded-none data-[size=sm]:py-0.5-wrapper group/native-select relative w-fit has-[select:disabled]:opacity-50",
                props["className"]
            )}
            data-slot="native-select-wrapper"
            data-size={size}
        >
            <select
                data-slot="native-select"
                data-size={size}
                className="border-input placeholder:text-muted-foreground selection:bg-primary selection:text-primary-foreground dark:bg-input/30 dark:hover:bg-input/50 focus-visible:border-ring focus-visible:ring-ring/50 aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 aria-invalid:border-destructive dark:aria-invalid:border-destructive/50 h-8 w-full min-w-0 appearance-none rounded-none border bg-transparent py-1 pr-8 pl-2.5 text-xs transition-colors select-none focus-visible:ring-1 aria-invalid:ring-1 data-[size=sm]:h-7 data-[size=sm]:rounded-none data-[size=sm]:py-0.5 outline-none disabled:pointer-events-none disabled:cursor-not-allowed"
                {**restProps}
            >
                {props["children"]}
            </select>
            <HugeiconsIcon
                icon={UnfoldMoreIcon}
                className="text-muted-foreground top-1/2 right-2.5 size-4 -translate-y-1/2 pointer-events-none absolute select-none"
                aria-hidden="true"
                data-slot="native-select-icon"
                size={16}
            />
        </div>;
}

def:pub NativeSelectOption(props: any) -> JsxElement {
    attrs: dict[str, any] = {** props};
    return
        <option data-slot="native-select-option" {**attrs}>{props["children"]}</option>;
}

def:pub NativeSelectOptGroup(props: any) -> JsxElement {
    attrs: dict[str, any] = {** props};
    return
        <optgroup
            data-slot="native-select-optgroup"
            className={props["className"]}
            {**attrs}
        >
            {props["children"]}
        </optgroup>;
}
