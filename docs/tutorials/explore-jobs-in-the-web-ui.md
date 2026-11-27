# Explore jobs in the web UI

This tutorial walks one job from the search page to its metrics, plots and drill-down pages.
It is for researchers and HPC users on a cluster that HPCPerfStats tracks, who want to understand how a job ran.

We find a job, read the job list, then open the job page and visit each tab in the order that answers the most questions first.
Every metric, panel and plot named here has a full entry in [Web UI metrics](../reference/web-ui-metrics.md).

## Before you begin

You need:

- A login to the site's HPCPerfStats web UI.
- A job ID, or enough about a job (user, project, queue, end time) to search for it.

Not every job or architecture has every signal.
When data is missing, the panel or tab that would show it says so.

## Step 1: Find the job

To reach a job:

1. On the search home, browse by year or date to reach a filtered job list.
2. To open one job directly, enter its ID in **Find Job** in the navbar.
3. To find jobs by their properties, open **Extended search** in the navbar.
   You can filter by job ID, host, user, **Project**, state, queue, job end time window, runtime, node count, node-hours and derived metric thresholds.
   The **?** icon next to each field defines it.
   Active criteria combine with AND: a job must match all of them.
   The job end time fields filter on the scheduler end time, not on submit or start time.
   Derived metric choices match the **Metrics** tab and search prepared job-level metric summaries.

In **Extended search**, a job ID opens that one job.
A host together with a start for the job end time window opens the [host plot](../reference/web-ui-metrics.md#host-plot) for that host instead of a job list.

You see a job list, a job page or a host plot.

## Step 2: Read the job list

A filtered list shows an **Active filters** box above the table with the criteria in plain language.
Use **Modify search** to reopen **Extended search**.
When nothing matches, the table says **No jobs match these filters**; this is an empty result, not an error.

The table columns are **Job ID**, **Performance data**, **User**, **Project**, **nodes**, **cores**, **run time (HH:MM:SS)**, **start time**, **end time**, **queue**, **status**, **node hrs** and **name**.
Run time hours can pass 24 for long jobs.

The histogram thumbnails for runtime, node count and queue wait show whether a job is typical for the filter.

The **Performance data** column says how far each job got:

| Label                                 | Meaning                                                    |
| ------------------------------------- | ---------------------------------------------------------- |
| **Metrics & Plots available**         | Metrics and plots are ready.                               |
| **Metrics available**                 | Metric values exist; plots are still being prepared.       |
| **Too short to complete**             | The job ran too briefly to compute metrics.                |
| **Too few samples to complete**       | The monitor recorded too few samples to compute metrics.   |
| **Metrics & Plots not yet completed** | Metrics and plots have not been computed for this job yet. |

Pick a row marked **Metrics & Plots available** for the rest of this tutorial, and open it by its **Job ID**.

## Step 3: Read the job overview

The job page opens with breadcrumbs (**Browse** > **Job** and the ID); **Browse** returns to the search home.

Read **Job overview** first: status, runtime, queue, cores and nodes, user and project, start and end.
These scheduler fields frame the run before you look at telemetry; [Scheduler fields](../reference/web-ui-metrics.md#scheduler-fields) says what each one tells you.

The tabs under **Job data** (Metrics, Summary plots, Roofline, Multiprecision Mix, Processes, Execution and hosts, Device data) sync to the URL as `?tab=`.
Copy the URL to bookmark or share the job page open on the same tab.

## Step 4: Scan the Summary plots

Open **Summary plots**.
It is the fastest view of phase changes and node-to-node divergence across CPU, memory, I/O, network and GPU signals.
Each line is one host over time, on the cluster's time zone.

The three plot tabs (Summary plots, Roofline, Multiprecision Mix) load only when **Performance data** is **Metrics & Plots available**.
Otherwise the tab says **Plots not yet completed.** (still being prepared) or **No plots available for this job** (too short or too few samples).
If a ready plot fails to load, **Retry plots** reloads that section without reloading the page.

You see one subplot per signal, with a **?** help marker on each.

## Step 5: Check the Roofline

Open **Roofline**.
It holds a CPU roofline and, when the counters exist, a GPU roofline.
Points near the sloped line are bandwidth limited; points near the flat top are compute limited.
[Roofline tab](../reference/web-ui-metrics.md#roofline-tab) explains how to read both charts.

## Step 6: Check the Multiprecision Mix

Open **Multiprecision Mix**.
The CPU and GPU pies show which precisions the busy arithmetic used.
Compare it with an earlier run when the code or model changed its precision policy.

## Step 7: Read the Metrics tab

Open **Metrics** for scalar job-level summaries: averages, peaks, imbalance percentages, GPU detail and file system rows.
Sections run CPU, GPU, File System, Network, Misc.
Rows with a value come first; rows without one sit in the collapsed **Metrics not computed** table at the bottom of each section.
Use the **?** icon on a row for its definition, and [Job-level metrics](../reference/web-ui-metrics.md#job-level-metrics) for its diagnostic use.

## Step 8: Drill into devices, processes and hosts

To check what ran and what the monitor recorded:

1. Open **Device data** and select a type name to open its type detail page (`/machine/job/<JID>/<TYPE>/`) with raw event families and type-level plots and tables.
2. Open **Processes** to confirm which executables ran and their peak memory.
3. Open **Execution and hosts** for the XALT executable path, working directory, library set and host list.

`<JID>` is the job ID and `<TYPE>` the monitor type name.

## Step 9: Print a snapshot

Select **Print** next to the breadcrumbs.
The browser print dialog opens with Job overview, Resources, Metrics, Summary plots, Roofline and Multiprecision Mix, ready to print or save as PDF for a ticket or review.
Processes, Execution and hosts, Device data and the **Client / Server** log links are left out.

When **Performance data** is **Metrics available**, the snapshot leaves out the plot tabs.
For a job without metrics, Print opens a dialog that says the job has no data to print.

## What you built

You found a job, read where it stands in the job list, and walked its overview, plots, metrics and drill-down pages.
With that path you can triage a slow or failed job and hand a printed snapshot to support.

## Related

- [Web UI metrics](../reference/web-ui-metrics.md)
- [Symptom checklist](../reference/web-ui-metrics.md#symptom-checklist)
- [Architecture](../explanation/architecture.md)
