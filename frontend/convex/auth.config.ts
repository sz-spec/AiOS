export default {
  providers: [
    {
      domain: process.env.CLERK_ISSUER_URL ?? `https://clerk.${process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY?.split("_")[2]}.lcl.dev`,
      applicationID: "convex",
    },
  ],
};
