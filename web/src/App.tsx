const columns = [
  { name: "Backlog", tasks: ["Define SQLite event schema", "Add execution evidence model"] },
  { name: "Ready", tasks: ["Implement task_claim MCP tool"] },
  { name: "In progress", tasks: ["Create domain transition tests"] },
  { name: "Verifying", tasks: ["Review lease expiry contract"] },
  { name: "Done", tasks: ["Bootstrap AgentBoard repository"] },
];

export function App() {
  return (
    <main className="app-shell">
      <header>
        <p>AgentBoard · local project</p>
        <h1>Execution board</h1>
        <span className="live">MCP connected</span>
      </header>
      <section className="board" aria-label="AgentBoard task board">
        {columns.map((column) => (
          <article className="column" key={column.name}>
            <h2>{column.name}<span>{column.tasks.length}</span></h2>
            {column.tasks.map((task) => <button className="task" key={task}>{task}</button>)}
          </article>
        ))}
      </section>
    </main>
  );
}

