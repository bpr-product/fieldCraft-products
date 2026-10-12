/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 14-07-2026
 * @author      : Karthika S
 * @since       : 0.0.1
 * Requirement# : AB#24561
 * Purpose      : craco webpack step for fieldcraft-tenant-web. Production builds drop the
 *                in-build type-checker; styles are left exactly as CRA builds them.
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |   DATE        |   AUTHOR          |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 * AB#32217       |  29-09-2026   | Shanmugapriyan G  | Drop ForkTsChecker in production; no PostCSS override.
 */

const isForkTsChecker = (plugin) =>
  Boolean(plugin) && plugin.constructor && plugin.constructor.name === 'ForkTsCheckerWebpackPlugin';

module.exports = {
  webpack: {
    // Production type checking runs as `npm run typecheck` in the pipeline, not inside webpack.
    configure: (webpackConfig, { env }) => {
      if (env === 'production') {
        webpackConfig.plugins = webpackConfig.plugins.filter((plugin) => !isForkTsChecker(plugin));
      }
      return webpackConfig;
    },
  },
};
