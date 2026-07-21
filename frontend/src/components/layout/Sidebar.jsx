const menuItems = [
  "Dashboard",
  "AI Workspace",
  "Mission Center",
  "Docker",
  "Files",
  "System",
  "Settings",
];

export default function Sidebar() {
  return (
    <aside className="sidebar">
      <div className="sidebar-logo">
        <h2>NUTTZ OS</h2>
        <span>v2 Foundation</span>
      </div>

      <nav className="sidebar-nav">
        {menuItems.map((item) => (
          <button
            key={item}
            className="sidebar-button"
          >
            {item}
          </button>
        ))}
      </nav>
    </aside>
  );
}