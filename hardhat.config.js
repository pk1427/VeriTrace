require("@nomicfoundation/hardhat-chai-matchers");
require("@nomicfoundation/hardhat-ethers");

const { vars: hardhatVars } = require("hardhat/config");

module.exports = {
  solidity: {
    version: "0.8.24",
    settings: { optimizer: { enabled: true, runs: 200 } },
  },
  defaultNetwork: "hardhat",
  networks: {
    localhost: { url: "http://127.0.0.1:8545" },
    amoy: {
      url: process.env.POLYGON_AMOY_RPC_URL || process.env.POLYGON_RPC_URL || "",
      accounts: process.env.PRIVATE_KEY ? [process.env.PRIVATE_KEY] : [],
      chainId: process.env.POLYGON_AMOY_CHAIN_ID
        ? Number(process.env.POLYGON_AMOY_CHAIN_ID)
        : 80002,
    },
  },
};
