# Deploy Rysk IV Tracker to Vercel (Free)

## Prerequisites
- GitHub account
- Vercel account (free): https://vercel.com
- Supabase account (free): https://supabase.com

## Step 1: Create Supabase Database (Free)

1. Go to https://supabase.com and sign up/login
2. Click "New Project"
3. Choose a name (e.g., `rysk-iv-tracker`)
4. Set a strong database password (save this!)
5. Select a region close to you
6. Click "Create new project"
7. Wait for project to be ready (~2 minutes)

### Get Database URL:
1. Go to **Settings** > **Database**
2. Scroll to **Connection string** > **URI**
3. Copy the connection string
4. Replace `[YOUR-PASSWORD]` with your database password

Example:
```
postgresql://postgres:YourPassword123@db.abcdefgh.supabase.co:5432/postgres
```

## Step 2: Push Code to GitHub

```bash
cd /Users/carnation/Documents/CLAUDE/Byscuit

# Initialize git repo (if not already)
git init

# Create .gitignore
echo "data/
*.db
__pycache__/
.env
*.pyc
.DS_Store
node_modules/
" > .gitignore

# Add and commit
git add .
git commit -m "Rysk IV Tracker - ready for Vercel deployment"

# Create GitHub repo and push
# Go to https://github.com/new and create a new repo
# Then:
git remote add origin https://github.com/YOUR_USERNAME/rysk-iv-tracker.git
git branch -M main
git push -u origin main
```

## Step 3: Deploy to Vercel

1. Go to https://vercel.com and sign in
2. Click "Add New" > "Project"
3. Import your GitHub repository
4. Configure the project:
   - **Framework Preset**: Other
   - **Root Directory**: ./
   - **Build Command**: (leave empty)
   - **Output Directory**: (leave empty)

5. Add Environment Variables:
   - Click "Environment Variables"
   - Add:
     - `DATABASE_URL` = your Supabase connection string
     - `CRON_SECRET` = a random secret string (e.g., `sk_live_abc123xyz789`)

6. Click "Deploy"

## Step 4: Verify Deployment

1. Once deployed, visit your site: `https://your-project.vercel.app`
2. You should see the dashboard (empty at first)

## Step 5: Trigger First Data Fetch

Run this command to fetch initial data:

```bash
curl -X POST https://your-project.vercel.app/api/fetch \
  -H "Authorization: Bearer YOUR_CRON_SECRET"
```

Or visit: `https://your-project.vercel.app/api/cron/fetch` (will work once cron is set up)

## Step 6: Set Up Hourly Cron (Important!)

The `vercel.json` includes a cron job, but Vercel's free tier only allows **daily** crons.

### Option A: Upgrade to Pro ($20/mo)
- Vercel Pro allows hourly crons
- The config in `vercel.json` will work automatically

### Option B: Use Free External Cron Service (Recommended)

Use **cron-job.org** (free):

1. Go to https://cron-job.org and sign up
2. Click "Create Cronjob"
3. Configure:
   - **Title**: Rysk IV Fetch
   - **URL**: `https://your-project.vercel.app/api/cron/fetch`
   - **Schedule**: Every hour (0 * * * *)
   - **Request Method**: GET
   - **Headers**: Add `Authorization: Bearer YOUR_CRON_SECRET`
4. Save and enable

### Option C: Use GitHub Actions (Free)

Create `.github/workflows/fetch.yml`:

```yaml
name: Fetch IV Data
on:
  schedule:
    - cron: '0 * * * *'  # Every hour
  workflow_dispatch:  # Manual trigger

jobs:
  fetch:
    runs-on: ubuntu-latest
    steps:
      - name: Trigger fetch
        run: |
          curl -X POST ${{ secrets.VERCEL_URL }}/api/fetch \
            -H "Authorization: Bearer ${{ secrets.CRON_SECRET }}"
```

Add secrets in GitHub repo settings:
- `VERCEL_URL`: Your Vercel deployment URL
- `CRON_SECRET`: Your cron secret

## Monitoring

- **Dashboard**: `https://your-project.vercel.app`
- **API Endpoints**:
  - `/api/assets` - List tracked assets
  - `/api/latest` - Latest IV values
  - `/api/iv/BTC?days=7` - Historical IV for asset
  - `/api/cron/fetch` - Trigger data fetch

## Troubleshooting

### "No data" on dashboard
- Trigger a manual fetch first
- Check Vercel logs for errors

### Database connection errors
- Verify DATABASE_URL is correct
- Check Supabase project is active (free tier pauses after 1 week of inactivity)

### Cron not running
- Verify cron service is configured
- Check CRON_SECRET matches in both places

## Cost Summary

| Service | Cost |
|---------|------|
| Vercel (Hobby) | Free |
| Supabase (Free tier) | Free |
| cron-job.org | Free |
| **Total** | **$0/month** |

## Limits (Free Tier)

- **Vercel**: 100GB bandwidth/month, 100 hours serverless execution
- **Supabase**: 500MB database, 2GB bandwidth, pauses after 1 week inactivity
- **cron-job.org**: Unlimited cron jobs

For most use cases, these limits are more than sufficient.
