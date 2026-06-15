#!/usr/bin/env pwsh
# Deploy updated application to Databricks

Write-Host "🚀 Deploying Smart Shopping Assistant to Databricks..." -ForegroundColor Cyan

# Check if databricks CLI is available
if (!(Get-Command databricks -ErrorAction SilentlyContinue)) {
    Write-Host "❌ Databricks CLI not found. Installing..." -ForegroundColor Red
    pip install databricks-cli
}

Write-Host "`n📦 Syncing files to Databricks workspace..." -ForegroundColor Yellow

# Option 1: Use databricks-sync (if available)
if (Get-Command databricks-sync -ErrorAction SilentlyContinue) {
    databricks-sync push
    Write-Host "✅ Files synced successfully!" -ForegroundColor Green
} else {
    Write-Host "⚠️  databricks-sync not found. Please deploy manually:" -ForegroundColor Yellow
    Write-Host "   1. Go to Databricks Workspace UI" -ForegroundColor White
    Write-Host "   2. Navigate to your app" -ForegroundColor White
    Write-Host "   3. Upload these updated files:" -ForegroundColor White
    Write-Host "      - core/workflow.py" -ForegroundColor Cyan
    Write-Host "      - core/nodes.py" -ForegroundColor Cyan
    Write-Host "      - core/rag_utils.py" -ForegroundColor Cyan
    Write-Host "      - core/prompts/response_generation.txt" -ForegroundColor Cyan
    Write-Host "   4. Restart the app" -ForegroundColor White
}

Write-Host "`n📋 Changes deployed:" -ForegroundColor Green
Write-Host "   ✓ More flexible category matching (case-insensitive, plural/singular)" -ForegroundColor White
Write-Host "   ✓ Increased search results from 10 to 50" -ForegroundColor White
Write-Host "   ✓ Fallback search strategy (relaxed filters → no filters)" -ForegroundColor White
Write-Host "   ✓ Post-filtering by price range" -ForegroundColor White
Write-Host "   ✓ Better color and material matching" -ForegroundColor White
Write-Host "   ✓ Fixed product details extraction from Pinecone metadata" -ForegroundColor White
Write-Host "   ✓ Chat messages now show actual product names and prices" -ForegroundColor White
Write-Host "   ✓ Dynamic product count - describes only available products (no placeholders)" -ForegroundColor White
Write-Host "   ✓ Limited to top 3 products for better quality responses" -ForegroundColor White

Write-Host "`n🧪 Test the app with:" -ForegroundColor Yellow
Write-Host "   - 'blue tote bag under 500'" -ForegroundColor Cyan
Write-Host "   - 'red leather shoulder bag'" -ForegroundColor Cyan
Write-Host "   - 'black backpack'" -ForegroundColor Cyan

Write-Host "`n✨ Deployment complete!" -ForegroundColor Green
