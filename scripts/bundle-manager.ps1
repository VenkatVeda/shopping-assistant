# Databricks Bundle Quick Setup Script
# This script helps you quickly validate and deploy to different environments

param(
    [Parameter(Mandatory=$false)]
    [ValidateSet('dev', 'test', 'prod')]
    [string]$Environment = 'dev',
    
    [Parameter(Mandatory=$false)]
    [ValidateSet('deploy', 'validate', 'destroy', 'summary', 'dry-run')]
    [string]$Action = 'deploy',
    
    [switch]$Force
)

# Colors for output
$colors = @{
    Info = 'Cyan'
    Success = 'Green'
    Warning = 'Yellow'
    Error = 'Red'
}

function Write-ColorOutput($Message, $Type = 'Info') {
    Write-Host $Message -ForegroundColor $colors[$Type]
}

# Header
Write-ColorOutput "`n========================================" 'Info'
Write-ColorOutput "  Databricks Bundle Manager" 'Info'
Write-ColorOutput "========================================`n" 'Info'

# Build command
$cmd = "databricks bundle $Action"

if ($Environment -ne 'dev') {
    $cmd += " -t $Environment"
}

if ($Force -and $Action -eq 'deploy') {
    $cmd += " --force"
}

# Display info
Write-ColorOutput "Environment: $Environment" 'Info'
Write-ColorOutput "Action: $Action" 'Info'
Write-ColorOutput "Command: $cmd`n" 'Info'

# Confirm for production
if ($Environment -eq 'prod' -and ($Action -eq 'deploy' -or $Action -eq 'destroy')) {
    Write-ColorOutput "⚠️  WARNING: You are about to $Action to PRODUCTION!" 'Warning'
    $confirmation = Read-Host "Type 'yes' to confirm"
    
    if ($confirmation -ne 'yes') {
        Write-ColorOutput "`n❌ Operation cancelled.`n" 'Error'
        exit 1
    }
}

# Execute
try {
    Write-ColorOutput "`n🚀 Executing...`n" 'Success'
    
    # Run the command
    Invoke-Expression $cmd
    
    if ($LASTEXITCODE -eq 0) {
        Write-ColorOutput "`n✅ $Action completed successfully for $Environment!`n" 'Success'
        
        # Show next steps
        if ($Action -eq 'deploy') {
            Write-ColorOutput "📍 Next Steps:" 'Info'
            Write-ColorOutput "  1. View your app in Databricks workspace" 'Info'
            Write-ColorOutput "  2. Check logs: databricks apps logs $Environment-shopping-assistant" 'Info'
            Write-ColorOutput "  3. Test the application`n" 'Info'
        }
    } else {
        Write-ColorOutput "`n❌ $Action failed with exit code $LASTEXITCODE`n" 'Error'
        exit $LASTEXITCODE
    }
    
} catch {
    Write-ColorOutput "`n❌ Error: $_`n" 'Error'
    exit 1
}

# Quick usage guide
Write-ColorOutput "`n📚 Quick Reference:" 'Info'
Write-ColorOutput "  Deploy to dev:    .\bundle-manager.ps1" 'Info'
Write-ColorOutput "  Deploy to test:   .\bundle-manager.ps1 -Environment test" 'Info'
Write-ColorOutput "  Deploy to prod:   .\bundle-manager.ps1 -Environment prod" 'Info'
Write-ColorOutput "  Validate:         .\bundle-manager.ps1 -Action validate -Environment prod" 'Info'
Write-ColorOutput "  Dry run:          .\bundle-manager.ps1 -Action dry-run -Environment prod" 'Info'
Write-ColorOutput "  View summary:     .\bundle-manager.ps1 -Action summary -Environment prod`n" 'Info'
