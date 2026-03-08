import { Link, useNavigate } from "react-router-dom";

export default function Layout({ session, children }) {
  const navigate = useNavigate();
  return (
    <div className="container-fluid">
      <nav className="navbar navbar-default" role="navigation">
        <div className="navbar-header">
          <a href="https://www.tacc.utexas.edu">
            <img
              src="/media/logo.png"
              alt="TACC"
              style={{ maxWidth: "50%" }}
            />
          </a>
        </div>
        <center>
          {session?.machine_name || "HPCPerfStats"}
          {session?.is_staff && (
            <>
              {" "}
              <Link to="/admin_monitor">Admin Monitor</Link>
            </>
          )}
        </center>
        <form
          className="navbar-form navbar-right"
          role="search"
          onSubmit={(e) => {
            e.preventDefault();
            const jid = e.target.jid?.value?.trim();
            if (jid) navigate(`/job/${jid}`);
          }}
        >
          <div className="form-group">
            <input
              type="text"
              className="form-control"
              name="jid"
              placeholder="Job ID"
            />
          </div>
          <button type="submit" className="btn btn-default">
            find job
          </button>
        </form>
      </nav>
      {children}
    </div>
  );
}
