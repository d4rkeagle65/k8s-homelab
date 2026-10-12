// Renovate's global (self-hosted) settings. What it updates, and how, is the
// repository's own renovate.json at the repo root.
module.exports = {
  platform: 'github',
  repositories: ['d4rkeagle65/k8s-homelab'],
  // renovate.json is in the repo; no onboarding pull request.
  onboarding: false,
  requireConfig: 'required',
  // Tools a task needs are installed at run time (renovate.json's installTools).
  binarySource: 'install',
  cacheDir: '/data/cache',
  repositoryCache: 'enabled',
  // The run's report: every dependency found, with its available updates.
  // report-to-metrics.py turns it into the metrics Prometheus reads.
  reportType: 'file',
  reportPath: '/data/report.json',
  // A captured release's files are regenerated after an update (renovate.json's
  // postUpgradeTasks); only these two commands may run.
  allowedCommands: [
    '^python3 -m pip install --quiet --target /tmp/renovate-py -r scripts/requirements\\.txt$',
    '^python3 scripts/renovate_post_upgrade\\.py --deps /tmp/renovate-py$',
  ],
};
