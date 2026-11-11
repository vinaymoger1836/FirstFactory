import { useTheme, type ThemeChoice } from "../lib/theme";
import { IconMonitor, IconMoon, IconSun } from "./Icons";

const OPTIONS: { value: ThemeChoice; label: string; Icon: typeof IconSun }[] = [
  { value: "system", label: "Use system theme", Icon: IconMonitor },
  { value: "light", label: "Light theme", Icon: IconSun },
  { value: "dark", label: "Dark theme", Icon: IconMoon },
];

/** Native radio group: arrow keys move between options, Tab leaves the group. */
export function ThemeSwitch() {
  const [choice, setChoice] = useTheme();
  return (
    <fieldset className="theme-switch">
      <legend className="visually-hidden">Colour theme</legend>
      {OPTIONS.map(({ value, label, Icon }) => (
        <label key={value} title={label}>
          <input
            type="radio"
            name="theme"
            value={value}
            checked={choice === value}
            onChange={() => setChoice(value)}
            aria-label={label}
          />
          <Icon />
        </label>
      ))}
    </fieldset>
  );
}
