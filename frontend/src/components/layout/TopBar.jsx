export default function TopBar() {
  return (
    <header className="topbar">
      <div className="topbar-left">
        <h1>NUTTZ OS</h1>
      </div>

      <div className="topbar-right">
        <span className="status online">🟢 Local AI Online</span>
      </div>
    </header>
  );
}