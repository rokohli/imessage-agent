import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  BrowserRouter,
  NavLink,
  Navigate,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import {
  Activity,
  Contacts,
  Drafts,
  Overview,
  Schedules,
  SettingsPage,
} from "./pages";
import "./style.css";
const links = [
  ["/", "◫", "Overview"],
  ["/schedules", "◷", "Schedules"],
  ["/contacts", "♧", "Contacts"],
  ["/drafts", "✎", "AI Drafts"],
  ["/approvals", "☑", "Approvals"],
  ["/activity", "↗", "Activity"],
  ["/settings", "⚙", "Settings"],
];
export function App() {
  const location = useLocation();
  const [dark, setDark] = useState(
    () =>
      localStorage.getItem("theme") === "dark" ||
      (!localStorage.getItem("theme") &&
        matchMedia("(prefers-color-scheme: dark)").matches),
  );
  const title =
    links.find(([path]) => path === location.pathname)?.[2] ?? "Overview";
  return (
    <div className={`app ${dark ? "dark" : ""}`}>
      <a className="skip" href="#content">
        Skip to content
      </a>
      <aside>
        <div className="brand">
          <span className="brand-icon">⌁</span>
          <div>
            iMessage Agent<small>PERSONAL & LOCAL</small>
          </div>
        </div>
        <nav aria-label="Main navigation">
          {links.map(([to, icon, label]) => (
            <NavLink to={to} end key={to}>
              <span aria-hidden="true">{icon}</span>
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          <span className="local-dot" /> On this Mac only
          <p>Human approved. Always.</p>
          <button
            onClick={() =>
              setDark((v) => {
                localStorage.setItem("theme", v ? "light" : "dark");
                return !v;
              })
            }
          >
            {dark ? "☀ Light appearance" : "☾ Dark appearance"}
          </button>
        </div>
      </aside>
      <div className="main">
        <header>
          <div>
            <p className="eyebrow">WORKSPACE</p>
            <h1>{title}</h1>
          </div>
          <span className="local-pill">
            <span className="local-dot" /> Local dashboard
          </span>
        </header>
        <main id="content" key={location.pathname}>
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/schedules" element={<Schedules />} />
            <Route path="/contacts" element={<Contacts />} />
            <Route path="/drafts" element={<Drafts />} />
            <Route path="/approvals" element={<Drafts approvals />} />
            <Route path="/activity" element={<Activity />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
        <footer>
          Private by design. Drafting, approval and execution stay separate.
        </footer>
      </div>
    </div>
  );
}
if (document.getElementById("root"))
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </StrictMode>,
  );
