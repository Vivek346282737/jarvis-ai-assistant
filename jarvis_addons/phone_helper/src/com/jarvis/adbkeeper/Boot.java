package com.jarvis.adbkeeper;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.util.Log;

/** Phone restarted / app installed / JARVIS says hello (with the home Wi-Fi's gateway): make sure the job is scheduled. */
public class Boot extends BroadcastReceiver {
    @Override
    public void onReceive(final Context c, Intent i) {
        String gw = i.getStringExtra("gw");
        if (gw != null && gw.matches("[0-9a-fA-F.:]{3,45}"))
            c.getSharedPreferences(Keep.PREFS, 0).edit().putString("gw", gw).remove("refused").apply();
        Keep.schedule(c);
        Log.i(Keep.TAG, "scheduled after " + i.getAction() + ", home gateway " + c.getSharedPreferences(Keep.PREFS, 0).getString("gw", "(none)"));
    }
}
