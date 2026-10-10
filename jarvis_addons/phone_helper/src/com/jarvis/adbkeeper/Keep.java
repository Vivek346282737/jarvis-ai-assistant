package com.jarvis.adbkeeper;

import android.app.job.JobInfo;
import android.app.job.JobParameters;
import android.app.job.JobScheduler;
import android.app.job.JobService;
import android.content.ComponentName;
import android.content.ContentResolver;
import android.content.Context;
import android.content.SharedPreferences;
import android.net.ConnectivityManager;
import android.net.LinkProperties;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.RouteInfo;
import android.provider.Settings;
import android.util.Log;

/**
 * JARVIS Link: after the phone restarts, Android switches "Wireless debugging" off (it does so on every phone: at boot the Wi-Fi is
 * not connected yet). This job runs when the phone is on the owner's home Wi-Fi and switches it on again, so that JARVIS on the
 * laptop can find the phone by itself. It does nothing else, has no screen and uses no internet.
 */
public class Keep extends JobService {
    static final String TAG = "JarvisLink", KEY = "adb_wifi_enabled", PREFS = "jarvis";
    static final long RETRY_AFTER_REFUSAL = 24L * 60 * 60 * 1000;

    static void schedule(Context c) {
        JobScheduler js = (JobScheduler) c.getSystemService(Context.JOB_SCHEDULER_SERVICE);
        ComponentName me = new ComponentName(c, Keep.class);
        js.schedule(new JobInfo.Builder(1, me).setRequiredNetworkType(JobInfo.NETWORK_TYPE_UNMETERED).setPersisted(true)
                .setPeriodic(15 * 60 * 1000L).build());
        js.schedule(new JobInfo.Builder(2, me).setRequiredNetworkType(JobInfo.NETWORK_TYPE_UNMETERED).setPersisted(true)
                .setMinimumLatency(0).setOverrideDeadline(5 * 60 * 1000L).build());       // right now / right after a restart
    }

    /** The gateway of the Wi-Fi the phone is on now, or null when it is not on Wi-Fi. */
    static String gateway(Context c) {
        ConnectivityManager cm = (ConnectivityManager) c.getSystemService(Context.CONNECTIVITY_SERVICE);
        for (Network n : cm.getAllNetworks()) {
            NetworkCapabilities caps = cm.getNetworkCapabilities(n);
            LinkProperties lp = cm.getLinkProperties(n);
            if (caps == null || lp == null || !caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) continue;
            for (RouteInfo r : lp.getRoutes())
                if (r.isDefaultRoute() && r.getGateway() instanceof java.net.Inet4Address && !r.getGateway().isAnyLocalAddress())   // the IPv4 one: the IPv6 route comes first on many routers
                    return r.getGateway().getHostAddress();
        }
        return null;
    }

    /** Switches Wireless debugging on when it is off and the phone is on the home Wi-Fi. Returns what happened (for the log). */
    static String apply(Context c) {
        try {
            ContentResolver cr = c.getContentResolver();
            SharedPreferences p = c.getSharedPreferences(PREFS, 0);
            String home = p.getString("gw", ""), now = gateway(c);
            if (Settings.Global.getInt(cr, KEY, 0) == 1) return "already on";
            if (now == null) return "not on Wi-Fi";
            if (home.isEmpty() || !home.equals(now)) return "not the home Wi-Fi (router " + now + ")";
            if (System.currentTimeMillis() - p.getLong("refused", 0) < RETRY_AFTER_REFUSAL && now.equals(p.getString("refused_gw", "")))
                return "Android refused it on this Wi-Fi earlier today";
            Settings.Global.putInt(cr, KEY, 1);
            try { Thread.sleep(2500); } catch (InterruptedException e) { }
            if (Settings.Global.getInt(cr, KEY, 0) == 1) {
                p.edit().remove("refused").apply();
                return "switched on";
            }
            p.edit().putLong("refused", System.currentTimeMillis()).putString("refused_gw", now).apply();   // not a trusted Wi-Fi for debugging
            return "Android switched it off again (this Wi-Fi is not trusted for debugging)";
        } catch (Throwable t) {
            return "error: " + t;
        }
    }

    @Override
    public boolean onStartJob(final JobParameters params) {
        new Thread(new Runnable() {
            public void run() {
                Log.i(TAG, "job " + params.getJobId() + ": " + apply(Keep.this));
                jobFinished(params, false);
            }
        }).start();
        return true;
    }

    @Override
    public boolean onStopJob(JobParameters params) {
        return false;
    }
}
