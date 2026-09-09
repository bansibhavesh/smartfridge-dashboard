# git-push.ps1
Write-Host "🚀 Pushing Smart Home Dashboard to GitHub..." -ForegroundColor Cyan

# Check if git is initialized
if (!(Test-Path ".git")) {
    Write-Host "📦 Initializing git repository..." -ForegroundColor Yellow
    git init
}

# Add remote if not exists
$remote = git remote get-url origin 2>$null
if (-not $remote) {
    Write-Host "🔗 Adding remote origin..." -ForegroundColor Yellow
    $repoUrl = Read-Host "Enter your GitHub repository URL (e.g., https://github.com/username/repo.git)"
    git remote add origin $repoUrl
}

# Stage all files
Write-Host "📝 Staging files..." -ForegroundColor Yellow
git add .

# Show status
Write-Host "📊 Current status:" -ForegroundColor Yellow
git status

# Commit
$commitMsg = Read-Host "Enter commit message (or press Enter for default)"
if ($commitMsg -eq "") {
    $commitMsg = "feat: Update Smart Home Dashboard with washing machine integration and activity log"
}
Write-Host "💾 Committing..." -ForegroundColor Yellow
git commit -m $commitMsg

# Push
Write-Host "🚀 Pushing to GitHub..." -ForegroundColor Yellow
git push -u origin main 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "⚠️ Push to main failed, trying master..." -ForegroundColor Yellow
    git push -u origin master
}

Write-Host "✅ Done!" -ForegroundColor Green