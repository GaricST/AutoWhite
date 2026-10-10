const manifest = {"name":"AutoWhite"};
const API_VERSION = 2;
const internalAPIConnection = window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit;
let api;
try {
    api = internalAPIConnection.connect(API_VERSION, manifest.name);
} catch (e) {
    api = internalAPIConnection.connect(1, manifest.name);
}
const callable = api.callable;
const definePlugin = (fn) => (...args) => fn(...args);

const getState = callable("get_state");
const setSettings = callable("set_settings");
const resetSettings = callable("reset_settings");

const LANG = (() => {
    const raw = (navigator.language || (navigator.languages && navigator.languages[0]) || "en").toLowerCase();
    return raw.indexOf("ru") === 0 ? "ru" : "en";
})();

const S = {
    title: ["Точка белого", "White point"],
    enabled: ["Включено", "Enabled"],
    enabledDesc: ["Экран теплее в темноте и нейтральнее при ярком свете", "Warmer in the dark, neutral in bright light"],
    source: ["Источник", "Source"],
    sourceSensor: ["Датчик освещённости", "Ambient light sensor"],
    sourceSchedule: ["Расписание", "Schedule"],
    warmth: ["Холоднее — Теплее", "Cooler — Warmer"],
    luxDark: ["Темно, лк", "Dark, lux"],
    luxBright: ["Светло, лк", "Bright, lux"],
    nightFrom: ["Ночь с, час", "Night from, hour"],
    nightTo: ["Ночь до, час", "Night to, hour"],
    state: ["Состояние", "State"],
    lux: ["Освещённость", "Illuminance"],
    white: ["Точка белого", "White point"],
    neutral: ["нейтральная", "neutral"],
    noSensor: ["датчик не найден", "sensor not found"],
    reset: ["Сбросить", "Reset"],
    help: ["Как это работает", "How it works"],
    helpText: ["Плагин читает датчик освещённости и пишет виртуальную точку белого в gamescope: в темноте экран теплее, при ярком свете нейтральнее. Яркость не меняется.",
        "The plugin reads the ambient light sensor and writes the gamescope virtual white point: the screen is warmer in the dark and more neutral in bright light. Brightness is untouched."]
};
const t = (key) => { const v = S[key]; return Array.isArray(v) ? v[LANG === "ru" ? 0 : 1] : key; };

const row = (child, key) => SP_JSX.jsx(DFL.PanelSectionRow, { children: child }, key);
const section = (title, children, key) => SP_JSX.jsx(DFL.PanelSection, { title: title, children: children }, key);

const Content = () => {
    const [state, setState] = SP_REACT.useState({});
    const [settings, setLocal] = SP_REACT.useState(null);
    const pending = SP_REACT.useRef(null);
    const timer = SP_REACT.useRef(null);

    const apply = (resp) => {
        if (!resp) return;
        setState(resp.state || {});
        setLocal(resp.settings || {});
    };
    const refresh = SP_REACT.useCallback(async () => {
        try { apply(await getState()); } catch (e) { }
    }, []);
    SP_REACT.useEffect(() => {
        refresh();
        const id = setInterval(refresh, 3000);
        return () => clearInterval(id);
    }, [refresh]);

    const flush = SP_REACT.useCallback(async () => {
        const patchData = pending.current;
        pending.current = null;
        if (!patchData) return;
        try { apply(await setSettings(patchData)); } catch (e) { }
    }, []);
    const patch = SP_REACT.useCallback((partial) => {
        setLocal((prev) => Object.assign({}, prev || {}, partial));
        pending.current = Object.assign({}, pending.current || {}, partial);
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => { timer.current = null; flush(); }, 400);
    }, [flush]);

    if (!settings) return SP_JSX.jsx("div", { children: "" });

    const st = state || {};
    const whiteText = st.white
        ? st.white[0].toFixed(4) + " / " + st.white[1].toFixed(4)
        : t("neutral");
    const luxText = (st.lux === null || st.lux === undefined)
        ? t("noSensor") : st.lux + (LANG === "ru" ? " лк" : " lx");
    const sensor = settings.source !== "schedule";

    const controls = [
        row(SP_JSX.jsx(DFL.ToggleField, {
            label: t("enabled"), description: t("enabledDesc"),
            checked: !!settings.enabled,
            onChange: (v) => { if (v !== !!settings.enabled) patch({ enabled: v }); }
        }), "en"),
        row(SP_JSX.jsx(DFL.SliderField, {
            label: t("warmth"), value: Math.round((settings.warmth || 0) * 100),
            min: 0, max: 100, step: 5, showValue: true,
            onChange: (v) => {
                // Панель иногда дёргает onChange при отрисовке: пишем только
                // если значение действительно изменилось.
                if (Math.round((settings.warmth || 0) * 100) !== v) patch({ warmth: v / 100 });
            }
        }), "warm")
    ];

    return SP_JSX.jsx(SP_REACT.Fragment, {
        children: [
            section(t("title"), controls, "main"),
            section(t("state"), [
                row(SP_JSX.jsx(DFL.Field, { label: t("lux"), children: luxText }), "lux"),
                row(SP_JSX.jsx(DFL.Field, { label: t("white"), children: whiteText }), "wp")
            ], "state")
        ]
    });
};

var index = definePlugin(() => ({
    name: "AutoWhite",
    titleView: SP_JSX.jsx("div", { className: DFL.staticClasses.Title, children: "AutoWhite" }),
    content: SP_JSX.jsx(Content, {}),
    icon: SP_JSX.jsx("span", { children: "◐" }),
    onDismount() { }
}));

export { index as default };
