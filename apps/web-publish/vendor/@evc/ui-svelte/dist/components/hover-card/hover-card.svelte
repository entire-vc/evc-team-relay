<script lang="ts">
  import { LinkPreview as HoverCardPrimitive } from 'bits-ui';

  type Props = HoverCardPrimitive.RootProps;

  let { children, ...restProps }: Props = $props();
</script>

<HoverCardPrimitive.Root {...restProps}>
  {@render children?.()}
</HoverCardPrimitive.Root>
