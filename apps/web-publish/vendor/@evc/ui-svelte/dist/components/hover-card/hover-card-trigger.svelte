<script lang="ts">
  import { LinkPreview as HoverCardPrimitive } from 'bits-ui';

  type Props = HoverCardPrimitive.TriggerProps & {
    class?: string;
  };

  let { children, ...restProps }: Props = $props();
</script>

<HoverCardPrimitive.Trigger {...restProps}>
  {@render children?.()}
</HoverCardPrimitive.Trigger>
